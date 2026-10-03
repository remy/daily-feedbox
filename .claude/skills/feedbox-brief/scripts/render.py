#!/usr/bin/env python3
"""Render a finished brief.md into brief.html using assets/brief-template.html.

Usage: python3 render.py <brief.md> [--out <brief.html>] [--date YYYY-MM-DD]

The markdown file is the source of truth. This script understands only the
subset the brief uses; anything else is an error, not a silent pass-through:

    # Title                     -> h1
    ## Section                  -> h2
    1. item                     -> ol.points > li
    - item                      -> ul > li
    paragraph text              -> p
    **bold**  `code`            -> strong, code
    [text][ref] / [text][]      -> a, resolved against the link definitions
    [ref]: https://...          -> link definition, stripped from the output

Exits non-zero on an unresolved link reference or an unsupported construct.
"""
import argparse
import datetime as dt
import html
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "brief-template.html"


def die(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


# --- inline ----------------------------------------------------------------

def inline(text, refs, seen):
    """Escape, then apply the inline subset. Order matters: code last-ish."""
    out = html.escape(text, quote=False)

    def ref_link(m):
        label, key = m.group(1), (m.group(2) or m.group(1)).strip().lower()
        if key not in refs:
            die(f"link reference [{key}] has no definition at the foot of the file")
        seen.add(key)
        return f'<a href="{html.escape(refs[key], quote=True)}">{label}</a>'

    out = re.sub(r"\[([^\]]+)\]\[([^\]]*)\]", ref_link, out)

    if re.search(r"\]\(https?://", out):
        die("inline link found — the brief uses reference-style links only")

    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)

    if "**" in out:
        die(f"unclosed bold: {text.strip()[:60]!r}")
    return out


# --- block -----------------------------------------------------------------

def render_blocks(lines, refs, seen, indent="  "):
    """Turn a run of markdown lines into HTML blocks."""
    out, i = [], 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue

        if line.startswith("#"):
            die(f"unexpected heading inside a section: {line!r}")

        if re.match(r"^\s*\d+[.)]\s+", line):
            items, i = collect_list(lines, i, r"^\s*\d+[.)]\s+")
            out.append(f'{indent}<ol class="points">')
            for it in items:
                out.append(f"{indent}  <li>{inline(it, refs, seen)}</li>")
            out.append(f"{indent}</ol>")
            continue

        if re.match(r"^\s*[-*+]\s+", line):
            items, i = collect_list(lines, i, r"^\s*[-*+]\s+")
            out.append(f"{indent}<ul>")
            for it in items:
                out.append(f"{indent}  <li>{inline(it, refs, seen)}</li>")
            out.append(f"{indent}</ul>")
            continue

        if line.lstrip().startswith(">") or line.lstrip().startswith("```") or line.lstrip().startswith("|"):
            die(f"unsupported block (quote, fence or table): {line.strip()[:60]!r}")

        para, i = collect_paragraph(lines, i)
        out.append(f"{indent}<p>{inline(para, refs, seen)}</p>")
    return out


def collect_list(lines, i, marker):
    """Collect one list, tight or loose.

    A blank line between numbered items is still one list. Breaking on it
    produced a separate <ol class="points"> per item, and because the CSS
    resets the counter on each list the talking points rendered "1. 1. 1. 1.".
    So a blank line only ends the list if what follows is not another item.
    """
    items = []
    while i < len(lines):
        line = lines[i]
        if re.match(marker, line):
            items.append(re.sub(marker, "", line).strip())
            i += 1
        elif line.strip() and line.startswith((" ", "\t")) and items:
            items[-1] += " " + line.strip()      # continuation line
            i += 1
        elif not line.strip():
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and re.match(marker, lines[j]):
                i = j                            # blank line inside the list
                continue
            break
        else:
            break
    return items, i


def collect_paragraph(lines, i):
    buf = []
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip() or line.startswith("#") or re.match(r"^\s*(\d+[.)]|[-*+])\s+", line):
            break
        buf.append(line.strip())
        i += 1
    return " ".join(buf), i


# --- document --------------------------------------------------------------

def parse(md):
    """Split into (title, sections, refs). Sections are (heading|None, lines)."""
    refs, body = {}, []
    for line in md.splitlines():
        m = re.match(r"^\[([^\]]+)\]:\s*(\S+)\s*$", line)
        if m:
            refs[m.group(1).strip().lower()] = m.group(2)
        else:
            body.append(line)

    title, sections, current = None, [], (None, [])
    for line in body:
        if line.startswith("# "):
            if title:
                die("more than one H1 — the brief has a single title")
            title = line[2:].strip()
        elif line.startswith("## "):
            sections.append(current)
            current = (line[3:].strip(), [])
        elif line.startswith("###"):
            die("H3 or deeper is not supported — the brief has one heading level below the title")
        else:
            current[1].append(line)
    sections.append(current)

    if not title:
        die("no H1 title found")
    return title, sections, refs


def british(date):
    return f"{date:%A} {date.day} {date:%B %Y}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("--out")
    ap.add_argument("--date", help="YYYY-MM-DD; defaults to the date in the filename")
    ap.add_argument("--fragment", action="store_true",
                    help="emit only <style> + <article>, for publishers that supply their own page skeleton")
    args = ap.parse_args()

    src = Path(args.brief)
    md = src.read_text()

    if args.date:
        stamp = args.date
    else:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", src.name)
        if not m:
            die("no date in the filename — pass --date YYYY-MM-DD (from meta.window.to)")
        stamp = m.group(1)
    try:
        date = dt.date.fromisoformat(stamp)
    except ValueError:
        die(f"bad date {stamp!r} — expected YYYY-MM-DD")

    title, sections, refs = parse(md)
    seen = set()

    # The dateline carries the date, so a dated H1 sheds its date in the HTML.
    display_title = title
    if "—" in title:
        head, _, tail = title.partition("—")
        if re.search(r"\b\d{4}\b", tail):
            display_title = head.strip()

    body = []
    for heading, lines in sections:
        if heading and heading.lower().startswith("provenance"):
            die("Provenance section in the markdown — it belongs in the run notes")
        if heading:
            body.append(f"  <h2>{inline(heading, refs, seen)}</h2>")
        body.extend(render_blocks(lines, refs, seen))

    unused = sorted(set(refs) - seen)
    if unused:
        die("unused link definitions: " + ", ".join(unused))

    out_html = TEMPLATE.read_text()

    # The template documents itself in an HTML comment, and that comment lists
    # the placeholder tokens by name. A plain str.replace substituted the whole
    # brief into that list as well, so every rendered page carried a second,
    # commented-out copy of itself and came out roughly twice the size. The
    # comment is for whoever edits the template, not for the reader: drop it
    # before substituting, and the duplication cannot happen again.
    out_html = re.sub(r"\n?<!--(?:(?!-->).)*?Rendered by scripts/render\.py"
                      r"(?:(?!-->).)*?-->\n?", "\n", out_html, flags=re.S)

    for key, value in {
        "{{PAGE_TITLE}}": html.escape(title, quote=False),
        "{{TITLE}}": html.escape(display_title, quote=False),
        "{{DATETIME}}": stamp,
        "{{DATELINE}}": british(date),
        "{{BODY}}": "\n".join(body),
    }.items():
        out_html = out_html.replace(key, value)

    leftover = re.findall(r"\{\{[A-Z_]+\}\}", out_html)
    if leftover:
        die("template placeholder left unsubstituted: " + ", ".join(sorted(set(leftover))))
    if body and out_html.count(body[0].strip()) != 1:
        die("the rendered body appears more than once — the template is "
            "substituting it somewhere besides <article>")

    if args.fragment:
        # Some publishers wrap the content in their own <html>/<head>/<body> and
        # reject a second one. The style block still carries `body` and `:root`
        # rules; injected into the host page those land on the host's own body,
        # which is what makes the brief read the same either way.
        style = re.search(r"<style>.*?</style>", out_html, re.S)
        article = re.search(r"<article>.*?</article>", out_html, re.S)
        if not style or not article:
            die("template no longer has a <style> and an <article> — --fragment needs both")
        out_html = style.group(0) + "\n\n" + article.group(0) + "\n"

    dest = Path(args.out) if args.out else src.with_suffix(".html")
    dest.write_text(out_html)
    kind = "fragment" if args.fragment else "page"
    print(f"rendered {dest} ({kind}) — {len(body)} body blocks, {len(refs)} links")


if __name__ == "__main__":
    main()
