#!/usr/bin/env python3
"""Verify a finished brief against the dump. Run before delivering.

Usage: python3 check.py <brief.md> <working.json>
Exits non-zero if any hard check fails.
"""
import json, re, sys, unicodedata, datetime as dt, os

def strip_md(t):
    t = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", t)   # reference links
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)     # inline links
    t = re.sub(r"^\[[^\]]+\]:.*$", "", t, flags=re.M)  # link definitions
    return re.sub(r"[*`_#>|-]", "", t)

def syllables(w):
    w = re.sub(r"[^a-z]", "", w.lower())
    if not w: return 0
    n, prev = 0, False
    for c in w:
        v = c in "aeiouy"
        if v and not prev: n += 1
        prev = v
    if w.endswith("e") and n > 1: n -= 1
    return max(n, 1)

brief = open(sys.argv[1]).read()
work = json.load(open(sys.argv[2]))
posts = work["posts"]
known = set()
for p in posts:
    known.add(p["url"]); known.add(p["clean_url"])

fails, warns = [], []

# 1. every link resolves to the dump, or is explicitly marked external
used = set(re.findall(r"^\[[^\]]+\]:\s*(\S+)", brief, flags=re.M)) or set(re.findall(r"\]\((https?://[^)]+)\)", brief))
for u in sorted(used):
    if u not in known:
        warns.append(f"link not in dump (must be flagged as external in the text): {u}")

# 2. no post without a fetched body is characterised
nobody = {p["clean_url"] for p in posts if not p["has_body"]}
for u in used & nobody:
    warns.append(f"link to a post with no fetched body — describe only as 'landed': {u}")

# 3. bold must not wrap a link
if re.search(r"\*\*\[[^\]]+\]", brief):
    fails.append("bold wraps a link — bold is for claims, links are separate")

# 4. reference-style links only
if re.search(r"\]\(https?://", brief):
    fails.append("inline link found — use reference-style definitions at the foot")

# 5. readability
body = strip_md(brief)
sents = [s for s in re.split(r"(?<=[.!?:])\s+", body) if len(s.split()) > 2]
words = re.findall(r"[A-Za-z']+", body)
if sents and words:
    syl = sum(syllables(w) for w in words)
    fre = 206.835 - 1.015*(len(words)/len(sents)) - 84.6*(syl/len(words))
    longest = max(len(re.findall(r"[A-Za-z']+", s)) for s in sents)
    avg = len(words)/len(sents)
    print(f"readability: Flesch {fre:.1f} | avg sentence {avg:.1f}w | longest {longest}w")
    if fre < 60: fails.append(f"Flesch {fre:.1f} below 60")
    if avg > 15: fails.append(f"average sentence {avg:.1f} words, over 15")
    if longest > 25: warns.append(f"longest sentence {longest} words")

# 6. no ranking language — the dump is flat, so the brief must not imply an order
for term in ("top-ranked", "highest ranked", "top of the ranking", "rank ", "ranked "):
    if term in brief.lower():
        warns.append(f"ranking language in the brief ({term.strip()!r}) — posts are unranked")

# 7. no source counting — grouping is handled upstream, counts are not the brief's business
for term in ("widely covered", "widely shared", "picked up by", "several feeds",
             "multiple feeds", "across feeds", "doing the rounds", "everyone is talking"):
    if term in brief.lower():
        warns.append(f"source-counting language ({term!r}) — do not report how many feeds carried a story")

# 8. report paragraph length — 40-80 words each
#    Short enough to read in a breath, long enough to carry a thread. Advisory:
#    a paragraph that earns its length is better than one padded or cut to fit.
report = re.split(r"^## ", brief, flags=re.M)[0]
report = re.sub(r"^#\s.*$", "", report, flags=re.M)          # drop the H1
report = re.sub(r"^\[[^\]]+\]:.*$", "", report, flags=re.M)   # drop link definitions
for i, para in enumerate([b.strip() for b in re.split(r"\n\s*\n", report) if b.strip()], 1):
    if re.match(r"^([-*+]|\d+\.)\s", para):
        continue
    n = len(re.findall(r"[A-Za-z0-9'\u2019-]+", strip_md(para)))
    if n < 40:
        warns.append(f"report paragraph {i} is {n} words, under 40 — thin for a thread")
    elif n > 80:
        warns.append(f"report paragraph {i} is {n} words, over 80 — split it or cut it")

# 9. relative days — the dump closes the evening before and the page may be read
#    later still, so every word anchored to "now" is wrong by at least a day.
#    Weekday names survive the gap; today/yesterday/overnight do not.
prose = re.sub(r"^\[[^\]]+\]:.*$", "", brief, flags=re.M)   # link definitions carry URLs, not prose
for term in ("today", "yesterday", "tomorrow", "tonight", "overnight", "last night",
             "this morning", "this afternoon", "this evening"):
    if re.search(rf"\b{term}\b", prose, flags=re.I):
        fails.append(f"relative day {term!r} — name the weekday instead (the dump is a day behind)")
for term in ("this week", "last week", "next week", "in recent days", "the past few days",
             "just now", "right now", "currently", "at the moment"):
    if re.search(rf"\b{term}\b", prose, flags=re.I):
        warns.append(f"relative time {term!r} — anchor it to a named day or a date")

# 10. the H1's weekday must be the real weekday for the brief's date, and the
#     date must be the window's. Naming the wrong day is the one error the
#     reader has no way to catch, and every other day-name in the prose is
#     anchored to this one.
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
h1 = re.search(r"^#\s+(.+)$", brief, flags=re.M)
if not h1:
    fails.append("no H1 title")
else:
    m = re.search(r"(\w+day)\s+(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})", h1.group(1))
    if not m:
        warns.append(f"H1 carries no '<Weekday> <D> <Month> <YYYY>' date: {h1.group(1)!r}")
    elif m.group(3) not in MONTHS:
        warns.append(f"H1 month not recognised: {m.group(3)!r}")
    else:
        titled = dt.date(int(m.group(4)), MONTHS[m.group(3)], int(m.group(2)))
        if titled.strftime("%A") != m.group(1):
            fails.append(f"H1 says {m.group(1)}, but {titled.isoformat()} is a "
                         f"{titled.strftime('%A')}")
        stamp = ((work.get("meta") or {}).get("window") or {}).get("to", "")[:10]
        if stamp and stamp != titled.isoformat():
            fails.append(f"H1 date {titled.isoformat()} is not the window's last day "
                         f"({stamp}) — the brief is dated from meta.window.to")
        fname = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(sys.argv[1]))
        if fname and fname.group(1) != titled.isoformat():
            fails.append(f"H1 date {titled.isoformat()} does not match the filename "
                         f"({fname.group(1)})")

for f in fails: print("FAIL:", f)
for w in warns: print("warn:", w)
sys.exit(1 if fails else 0)
