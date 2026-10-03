#!/usr/bin/env python3
"""Record what a finished brief linked and the threads it ran.

    python3 ledger.py <brief.md> <working.json> \
      --out covered-YYYY-MM-DD.json --covered <previous ledger> \
      --thread "slug|label|note"

Links come out of the brief automatically: every reference definition, matched
back to the dump so a title travels with the URL. Threads are written by hand,
one per report paragraph, because only the writer knows what a paragraph was
about. Those labels are what the next run reads to tell a follow-up from a
repeat, so they carry the weight here.

The previous ledger is merged in, oldest entries dropped past --keep days, and
the whole thing written to --out. It is append-only history: nothing is ever
rewritten, and a missing previous ledger is a first run, not an error.
"""
import json, re, sys, os, argparse, datetime as dt


def brief_date(path, meta):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    if m:
        return m.group(1)
    return str(((meta or {}).get("window") or {}).get("to", ""))[:10]


def links_in(brief):
    """Reference definitions, in the order they are defined.

    The brief is required to use reference links only, so the definition block
    is the complete set. Inline links are picked up too — if one has slipped
    past check.py the ledger should still record it rather than lose it.
    """
    out = []
    for u in re.findall(r"^\[[^\]]+\]:\s*(\S+)", brief, flags=re.M):
        out.append(u)
    for u in re.findall(r"\]\((https?://[^)]+)\)", brief):
        out.append(u)
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def parse_thread(s):
    parts = [x.strip() for x in s.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        sys.exit(f"BAD THREAD {s!r} — needs at least 'slug|label', note optional.")
    return {"slug": parts[0], "label": parts[1],
            "note": parts[2] if len(parts) > 2 else ""}


def load_covered(path, first_run=False):
    if not path:
        if not first_run:
            sys.exit("NO --covered GIVEN. The ledger is the only memory this brief "
                     "has, and writing one without merging the previous day wipes "
                     "it. Fetch the newest covered-*.json from feedbox/state/ and "
                     "pass it. If this genuinely is the first brief ever, pass "
                     "--first-run to say so deliberately.")
        return {"briefs": []}
    if not os.path.exists(path):
        if not first_run:
            sys.exit(f"LEDGER NOT FOUND at {path}. Do not carry on without it — "
                     f"the next run would read this brief as having no history. "
                     f"Check the path, or pass --first-run if there really is no "
                     f"previous ledger.")
        print(f"no ledger at {path} — treating this as the first brief.")
        return {"briefs": []}
    try:
        d = json.load(open(path))
    except (ValueError, OSError) as e:
        sys.exit(f"UNREADABLE LEDGER {path}: {e}. Fix or omit --covered; do not "
                 f"silently start a new one, the history matters.")
    d.setdefault("briefs", [])
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("working")
    ap.add_argument("--out", default=None,
                    help="output path (default covered-<brief date>.json)")
    ap.add_argument("--covered", default=None,
                    help="the ledger fetched at the start of the run")
    ap.add_argument("--thread", action="append", default=[],
                    metavar="slug|label|note",
                    help="one per report paragraph; repeatable")
    ap.add_argument("--keep", type=int, default=21,
                    help="days of history to carry forward (default 21)")
    ap.add_argument("--first-run", action="store_true",
                    help="there is no previous ledger and that is intended")
    a = ap.parse_args()

    brief = open(a.brief).read()
    work = json.load(open(a.working))
    meta = work.get("meta") or {}
    posts = work.get("posts") or []

    date = brief_date(a.brief, meta)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        sys.exit("NO DATE: name the brief brief-YYYY-MM-DD.md, or the working "
                 "file's meta.window.to must carry one.")

    by_url = {}
    for p in posts:
        for k in ("url", "clean_url"):
            if p.get(k):
                by_url[p[k]] = p

    urls = links_in(brief)
    if not urls:
        sys.exit("NO LINKS FOUND in the brief — nothing to record. Check the "
                 "reference definitions are at the foot of the file.")

    entries, external = [], 0
    for u in urls:
        p = by_url.get(u)
        if p:
            entries.append({"url": p.get("clean_url") or u,
                            "title": p.get("title", ""),
                            "source": p.get("source", "")})
        else:
            external += 1
            entries.append({"url": u, "title": "", "source": "", "external": True})

    threads = [parse_thread(t) for t in a.thread]
    if not threads:
        print("WARNING: no --thread given. The next run will see the links but "
              "not what they were about, so a syndicated repeat will read as new.")

    cov = load_covered(a.covered, a.first_run)
    incoming = {b.get("date") for b in cov["briefs"] if b.get("date")}
    briefs = [b for b in cov["briefs"] if b.get("date") != date]
    briefs.append({"date": date, "links": entries, "threads": threads})
    briefs.sort(key=lambda b: b.get("date", ""), reverse=True)

    cutoff = (dt.date.fromisoformat(date) - dt.timedelta(days=a.keep)).isoformat()
    kept = [b for b in briefs if b.get("date", "") >= cutoff]
    dropped = len(briefs) - len(kept)

    # History has been lost twice in the past without anything saying so: once
    # 11 entries at a stroke, once all 9. Nothing here should ever lose a brief
    # that is still inside the keep window, so if one goes missing, stop.
    kept_dates = {b.get("date") for b in kept}
    lost = sorted(d for d in incoming if d >= cutoff and d not in kept_dates)
    if lost:
        sys.exit("HISTORY LOST: these briefs were in the ledger you passed and "
                 "are not in the merged result, and the " f"{a.keep}-day cutoff "
                 f"({cutoff}) does not explain them: {', '.join(lost)}. Refusing "
                 f"to write a ledger that forgets them.")

    # A ledger that suddenly shrinks is the symptom the two past incidents
    # showed. Say so loudly rather than writing it quietly.
    if incoming and len(kept) < len(incoming):
        print(f"WARNING: carrying {len(kept)} brief(s) forward, fewer than the "
              f"{len(incoming)} in the ledger passed in. Everything dropped is "
              f"older than the {a.keep}-day cutoff, but check that is intended "
              f"and say so in the run notes.")

    # Bare-URL entries mean a previous run stripped titles and sources out of
    # the history. Tomorrow can still match URLs, but the thread labels are
    # what catch a syndicated repeat, so it is worth knowing.
    thin = [b.get("date") for b in kept
            if b.get("date") != date
            and b.get("links")
            and all(not e.get("title") and not e.get("external") for e in b["links"])]
    if thin:
        print("WARNING: carried-forward entries with no link titles or sources: "
              + ", ".join(sorted(thin)) + ". Something upstream stripped them.")

    out = a.out or f"covered-{date}.json"
    json.dump({"updated": date, "keep_days": a.keep, "briefs": kept},
              open(out, "w"), indent=1)

    print(f"{date}: {len(entries)} link(s), {external} outside the dump, "
          f"{len(threads)} thread(s).")
    for t in threads:
        print(f"  {t['slug']}: {t['label']}")
    print(f"history: {len(kept)} brief(s) carried forward"
          + (f", {dropped} past {a.keep} days dropped" if dropped else ""))
    print(f"wrote {out} — upload it to feedbox/state/ before delivering.")


if __name__ == "__main__":
    main()
