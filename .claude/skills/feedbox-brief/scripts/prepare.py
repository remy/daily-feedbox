#!/usr/bin/env python3
"""Flatten a Feedbox dump so every post is considered equally.

    python3 prepare.py <dump-dir> --out working.json

No score, no rank, no manual-add weighting, no cross-feed grouping. `score`,
`tier`, and all of `state` except `liked` are stripped before anything downstream
sees them, so the ordering in 01-index.json cannot leak back in as priority.
`liked` is kept and surfaced: it is the reader's own verdict, not the ranker's.

Fetch EVERY file in the dump directory first, including all body chunks.
The script reports which posts arrived without a body and refuses to pretend
a partial fetch is a whole one.

Output:
  - a flat inventory line per post (chronological)
  - an opening excerpt for every post, so nothing goes unseen, with the `ai`
    summary appended where one exists as supplementary context
  - working.json containing all posts with full bodies
"""
import json, sys, glob, os, re, argparse, datetime as dt

DATE_KEYS = ("published", "publishedAt", "published_at", "date", "updated", "timestamp")
DROP_KEYS = ("score", "tier")


def strip_tracking(u):
    return re.sub(r"[?&](utm_[^=]+|gift|ref|source)=[^&]*", "", u).rstrip("?&")


def post_date(p):
    for k in DATE_KEYS:
        v = p.get(k)
        if v:
            return str(v)
    return ""


def as_date(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def day_label(v, brief_day):
    """How the brief should name this day.

    The run's own clock is a day ahead of the material, so relative words are
    always wrong. Within the past week a weekday name is unambiguous; beyond
    that it is not, so fall back to a date.
    """
    d = as_date(v)
    if not d or not brief_day:
        return "?"
    delta = (brief_day - d).days
    if 0 <= delta <= 6:
        return d.strftime("%A")
    return f"{d.day} {d.strftime('%B')}"


def load_covered(path):
    """Read the ledger. A missing one is a first run, not a failure."""
    if not path:
        print("no --covered given: running without memory of previous briefs.\n")
        return {}
    if not os.path.exists(path):
        print(f"no ledger at {path} — running without memory of previous briefs.\n")
        return {}
    try:
        return json.load(open(path))
    except (ValueError, OSError) as e:
        print(f"WARNING: ledger at {path} is unreadable ({e}). Continuing without "
              f"it, and say so in the run notes — repeats will not be caught.\n")
        return {}


def wordcount(t):
    return len(re.findall(r"[A-Za-z0-9']+", t))


def flatten(t, n):
    t = re.sub(r"[`*#>\[\]]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n] + ("…" if len(t) > n else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--out", default="working.json")
    ap.add_argument("--excerpt", type=int, default=400,
                    help="characters of each post's opening to print (0 to skip)")
    ap.add_argument("--max-age-hours", type=float, default=36.0,
                    help="refuse a dump whose window ended longer ago than this (default 36)")
    ap.add_argument("--expect-date", default=None,
                    help="optional YYYY-MM-DD the window should end on; exact match")
    ap.add_argument("--covered", default=None,
                    help="ledger written by ledger.py; marks posts recent briefs linked")
    a = ap.parse_args()

    meta = json.load(open(os.path.join(a.dump, "00-meta.json")))
    idx = json.load(open(os.path.join(a.dump, "01-index.json")))

    bodies, present = {}, []
    for f in sorted(glob.glob(os.path.join(a.dump, "*bodies*.json"))):
        present.append(os.path.basename(f))
        for b in json.load(open(f)):
            bodies[b["id"]] = b.get("body", "")

    brief_day = as_date(meta["window"]["to"])

    for p in idx:
        for k in DROP_KEYS:
            p.pop(k, None)
        # `liked` survives — it is the reader's own verdict on a post they read.
        # Everything else in `state` (manualAdd, read, later) is discarded: how a
        # post entered the corpus is not a reason to write about it.
        p["liked"] = bool((p.get("state") or {}).get("liked"))
        p.pop("state", None)
        p["body"] = bodies.get(p["id"], "")
        p["body_loaded"] = p["id"] in bodies
        p["clean_url"] = strip_tracking(p["url"])
        p["has_body"] = p.get("bodyFormat") in ("markdown", "html")
        p["date"] = post_date(p)
        p["day_label"] = day_label(p["date"], brief_day)
        p["words"] = wordcount(p["body"])
        p["body_links"] = re.findall(r"\]\((https?://[^)]+)\)", p["body"])[:20]

    # The ledger is memory, not a blocklist: it marks, it never removes.
    ledger = load_covered(a.covered)
    linked = {}
    for b in ledger.get("briefs", []):
        d = as_date(b.get("date"))
        for e in b.get("links", []):
            u = e.get("url")
            if u and (u not in linked or d and linked[u] and d > linked[u]):
                linked[u] = d
    for p in idx:
        prev = linked.get(p["clean_url"]) or linked.get(p["url"])
        p["covered_on"] = prev.isoformat() if prev else None
        p["covered_days"] = (brief_day - prev).days if prev and brief_day else None

    c = meta["counts"]
    w = meta["window"]
    brief_date = w["to"][:10]
    ended = dt.datetime.fromisoformat(w["to"].replace("Z", "+00:00"))
    age_h = (dt.datetime.now(dt.timezone.utc) - ended).total_seconds() / 3600
    if age_h > a.max_age_hours:
        sys.exit(f"STALE DUMP: window ended {age_h:.1f}h ago ({brief_date}), limit is "
                 f"{a.max_age_hours:.0f}h. The overnight job has probably not run. "
                 f"Report the date found and that no fresh dump exists. Do not write a brief.")
    if age_h < 0:
        print(f"WARNING: window ends {abs(age_h):.1f}h in the future — check the NAS clock.")
    if a.expect_date and brief_date != a.expect_date:
        sys.exit(f"WRONG DUMP: window ends {brief_date}, expected {a.expect_date}.")

    print(f"window {w['from']} -> {w['to']}   (brief date: {brief_date}, {age_h:.1f}h old)")
    print(f"posts {len(idx)} | feeds {c['feeds']} | summarised {c['summarised']}")

    # meta.counts.posts is the NAS's pre-filter total: the dump keeps only the
    # posts above that night's mean score, so it is normally larger than the
    # index. The index is what exists. Say so, so no run reports a corrupt dump.
    if c.get("posts") not in (None, len(idx)):
        print(f"NOTE: 00-meta.json says counts.posts={c['posts']}, the index holds "
              f"{len(idx)}. That is expected — the NAS filters to posts above the "
              f"night's mean score and meta reports the total before filtering. "
              f"{len(idx)} is the real corpus. Not a fault, and not worth a run note "
              f"unless the gap is wildly out of line with other nights.")
    liked = [p for p in idx if p["liked"]]
    print("Flat run: score, tier, manualAdd, read and later have been stripped.")
    print(f"`liked` is kept: {len(liked)} post(s) the reader marked themselves.\n")

    # Day names, so the brief never has to count from the run's own clock —
    # which is a day ahead of the material and wrong by the time anyone reads it.
    if brief_day:
        print(f"THE BRIEF'S DAY: {brief_day.strftime('%A')} "
              f"{brief_day.day} {brief_day.strftime('%B %Y')} — call this day "
              f"'{brief_day.strftime('%A')}', never 'today'.")
    seen = []
    for d in sorted({as_date(p["date"]) for p in idx if as_date(p["date"])}, reverse=True):
        if len(seen) < 10:
            seen.append(d)
    if seen:
        print("DAY MAP — use these names; never today/yesterday/overnight:")
        for d in seen:
            mark = "  <- window ends here" if d == brief_day else ""
            print(f"  {d.isoformat()}  {day_label(d, brief_day)}{mark}")
    print()

    # A missed run is invisible otherwise: the dump is overwritten in place, so
    # the day it covered simply never gets written about and nothing says so.
    # The ledger is the only record of which days produced a brief.
    all_dates = sorted({as_date(b.get("date")) for b in ledger.get("briefs", [])
                        if as_date(b.get("date"))}, reverse=True)
    if all_dates and brief_day:
        gap = (brief_day - all_dates[0]).days
        if gap > 1:
            missed = [(all_dates[0] + dt.timedelta(days=n)).isoformat()
                      for n in range(1, gap)]
            print("!! MISSED BRIEF DAY(S): " + ", ".join(missed))
            print(f"   The newest ledger entry is {all_dates[0].isoformat()}, but this "
                  f"dump covers {brief_day.isoformat()}. No brief was written for the "
                  f"day(s) in between.")
            print("   Those dumps are gone — each night overwrites the last — so the "
                  "material cannot be recovered. Some of it may still be in this "
                  "window; treat anything from those days as uncovered.")
            print("   PUT THIS IN THE RUN NOTES AND IN THE NOTIFICATION. A silently "
                  "skipped day is the failure the reader cannot see.\n")
    elif not ledger.get("briefs"):
        print("!! NO LEDGER HISTORY. Either this is the first brief, or the ledger "
              "chain has broken. If briefs exist in feedbox/reports/ for recent days, "
              "it is the chain — say so in the run notes.\n")

    recent = sorted(ledger.get("briefs", []), key=lambda b: b.get("date", ""),
                    reverse=True)[:5]
    if recent:
        print("THREADS RECENT BRIEFS RAN — read these, not just the column.")
        print("The ledger matches URLs, so a syndicated repeat shows as new here.")
        for b in recent:
            label = day_label(b.get("date"), brief_day)
            ts = b.get("threads") or []
            print(f"  {label} ({b.get('date')}), {len(b.get('links', []))} link(s):")
            for t in ts:
                note = f" — {t['note']}" if t.get("note") else ""
                print(f"    {t.get('label', '?')}{note}")
            if not ts:
                print("    (no threads recorded)")
        marked = sum(1 for p in idx if p["covered_on"])
        print(f"\n{marked} post(s) in this dump were linked by one of those briefs. "
              f"That is a mark, not a veto: run it again when the story moved.\n")

    # completeness — every body chunk must be on disk
    expected = [p for p in idx if p["has_body"]]
    missing = [p for p in expected if not p["body_loaded"]]
    print(f"CHUNKS ON DISK ({len(present)}): {', '.join(present) or 'none'}")
    print(f"BODIES LOADED: {len(bodies)}/{len(idx)}")
    if missing:
        print(f"INCOMPLETE: {len(missing)} posts claim a fetched body but none arrived.")
        print("  Fetch the remaining chunks before writing. If they cannot be fetched,")
        print("  these posts are link-only and that must go in the run notes:")
        for p in missing[:20]:
            print(f"    {p['title'][:60]}")

    nb = [p for p in idx if not p["has_body"]]
    print(f"\nNO BODY AT SOURCE ({len(nb)}) — link only, never characterise:")
    for p in nb:
        print(f"  {p['source'][:20]:<20} {p['title'][:56]}")

    order = sorted(idx, key=lambda p: (p["date"], p["source"]))
    print(f"\nINVENTORY ({len(order)}, chronological — order carries no weight)")
    print("DATE        DAY         SOURCE               WORDS  L  SEEN  TITLE"
          "      (L = reader liked it, SEEN = days since a brief linked it)")
    for p in order:
        seen = f"{p['covered_days']}d" if p["covered_days"] is not None else (
            "yes" if p["covered_on"] else ".")
        print(f"{p['date'][:10]:<11} {p['day_label']:<11} {p['source'][:20]:<20} {p['words']:>5}  "
              f"{'L' if p['liked'] else '.'}  {seen:<4}  {p['title'][:60]}")

    if a.excerpt:
        print(f"\nOPENINGS ({a.excerpt} chars each — every post, so none goes unseen)")
        for p in order:
            print(f"\n--- {p['title'][:70]}  [{p['source']}]")
            print(flatten(p["body"], a.excerpt) if p["body"] else "(no body fetched)")
            if p["liked"]:
                print("    LIKED by the reader.")
            if p["covered_on"]:
                d = day_label(p["covered_on"], brief_day)
                print(f"    ALREADY LINKED in {d}'s brief ({p['covered_on']}) — "
                      f"run it again only for what has changed since.")
            if p.get("ai"):
                print(f"    context (ai summary, not the post's own words): {flatten(str(p['ai']), 300)}")

    json.dump({"meta": meta, "posts": idx}, open(a.out, "w"))
    print(f"\nwrote {a.out} ({len(idx)} posts, full bodies)")


if __name__ == "__main__":
    main()
