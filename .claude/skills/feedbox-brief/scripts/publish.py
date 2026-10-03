#!/usr/bin/env python3
"""Assemble the deployable site from a folder of rendered briefs.

    python3 publish.py <briefs-dir> --out site [--keep 14]

`<briefs-dir>` holds `brief-YYYY-MM-DD.html` files — the pages `render.py`
produced, as archived in Drive under `feedbox/reports/`. The newest becomes
`index.html`; every kept day also gets its own dated page, so a link sent to
someone keeps working after the next night overwrites the front page.

A short list of the other days is appended to each page. It uses the
template's own `h2` and `ul` styling, plus one extra rule for the current
day, so nothing here needs the stylesheet to change.

Netlify deploys replace the whole site: whatever is not in this directory
stops existing. That is deliberate — `--keep` is the archive depth, and days
older than that fall off the end.
"""
import argparse
import datetime as dt
import html
import re
import shutil
import sys
from pathlib import Path

NAME = re.compile(r"^brief-(\d{4}-\d{2}-\d{2})\.html$")

EXTRA_CSS = """
nav.archive { margin-top: 3.5em; border-top: 1px solid var(--rule); padding-top: 1.25em; }
nav.archive ul { margin: 0; padding: 0; list-style: none; }
nav.archive li { margin-bottom: 0.35em; font-family: var(--sans); font-size: 0.85em; }
nav.archive li.current { color: var(--muted); }
nav.archive li.current::after { content: " — you are here"; }
"""


def die(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def british(d):
    return f"{d:%A} {d.day} {d:%B}"


def nav_html(days, current):
    """The 'earlier briefs' list, as it appears at the foot of every page."""
    rows = []
    for d in days:
        label = html.escape(british(d))
        if d == current:
            rows.append(f'    <li class="current">{label}</li>')
        else:
            rows.append(f'    <li><a href="{d.isoformat()}.html">{label}</a></li>')
    return ('  <nav class="archive">\n'
            '    <h2>Every brief</h2>\n'
            '  <ul>\n' + "\n".join(rows) + "\n  </ul>\n  </nav>\n")


def build_page(src_html, days, current):
    if "</article>" not in src_html:
        die(f"{current}: no </article> in the rendered page — was it made with --fragment?")
    out = src_html.replace("</article>", nav_html(days, current) + "</article>", 1)
    if "</style>" not in out:
        die(f"{current}: no </style> in the rendered page")
    return out.replace("</style>", EXTRA_CSS + "</style>", 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("briefs", help="directory of brief-YYYY-MM-DD.html files")
    ap.add_argument("--out", default="site")
    ap.add_argument("--keep", type=int, default=14,
                    help="how many days of dated pages the site carries (default 14)")
    a = ap.parse_args()

    found = {}
    for f in sorted(Path(a.briefs).iterdir()):
        m = NAME.match(f.name)
        if m:
            try:
                found[dt.date.fromisoformat(m.group(1))] = f
            except ValueError:
                die(f"bad date in filename: {f.name}")
    if not found:
        die(f"no brief-YYYY-MM-DD.html files in {a.briefs}")

    days = sorted(found, reverse=True)[:a.keep]
    newest = days[0]

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for d in days:
        page = build_page(found[d].read_text(), days, d)
        (out / f"{d.isoformat()}.html").write_text(page)

    # index.html is the newest day, identical to its dated page so the two
    # never drift. Netlify serves the directory root from it.
    shutil.copyfile(out / f"{newest.isoformat()}.html", out / "index.html")

    (out / "_headers").write_text(
        "/index.html\n"
        "  Cache-Control: public, max-age=0, must-revalidate\n"
        "/*.html\n"
        "  Cache-Control: public, max-age=600\n"
    )

    dropped = len(found) - len(days)
    total = sum(p.stat().st_size for p in out.iterdir())
    print(f"front page: {newest.isoformat()} ({british(newest)})")
    print(f"wrote {out}/ — {len(days)} dated page(s) + index.html + _headers, "
          f"{total / 1024:.0f} KB"
          + (f", {dropped} older day(s) past --keep {a.keep} left off" if dropped else ""))
    missing = [(newest - dt.timedelta(days=n)).isoformat()
               for n in range(1, len(days))
               if (newest - dt.timedelta(days=n)) not in found]
    if missing:
        print("gaps in the archive (no brief was written for these): "
              + ", ".join(missing))


if __name__ == "__main__":
    main()
