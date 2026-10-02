#!/usr/bin/env node
/**
 * Build the Feedbox brief site from the markdown in briefs/.
 *
 *   node build.js [--src briefs] [--out dist] [--recent 7]
 *
 * Every briefs/YYYY-MM-DD.md becomes a dated page. The newest is also
 * index.html, and archive.html lists the lot. Nothing is pruned: drop a
 * markdown file in and it gets a page, so the archive only ever grows.
 *
 * The markdown is the source of truth, and only the subset the brief uses is
 * understood. Anything else is a build failure, not a silent pass-through:
 *
 *   # Title                 -> h1
 *   ## Section              -> h2
 *   1. item                 -> ol.points > li
 *   - item                  -> ul > li
 *   paragraph text          -> p
 *   **bold**  `code`        -> strong, code
 *   [text][ref] / [text][]  -> a, resolved against the link definitions
 *   [ref]: https://...      -> link definition, stripped from the output
 *
 * Ported from the feedbox-brief skill's render.py and publish.py. Keep the two
 * in step: render.py still produces the copy delivered in chat.
 */

import { readFileSync, writeFileSync, readdirSync, rmSync, mkdirSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(fileURLToPath(import.meta.url));

/* ---------------------------------------------------------------- failure */

class BuildError extends Error {}

const die = (msg) => {
  throw new BuildError(msg);
};

/* ------------------------------------------------------------------- dates */

// Deliberately not Intl: the output must not shift with the build container's
// locale or ICU build. British English, always.
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Parse YYYY-MM-DD as a UTC date, rejecting the ones that don't exist. */
function parseDate(stamp) {
  const m = ISO.exec(stamp);
  if (!m) die(`bad date ${JSON.stringify(stamp)} — expected YYYY-MM-DD`);
  const [, y, mo, d] = m.map(Number);
  const date = new Date(Date.UTC(y, mo - 1, d));
  // Date.UTC rolls 2026-02-31 forward into March rather than complaining.
  if (
    date.getUTCFullYear() !== y ||
    date.getUTCMonth() !== mo - 1 ||
    date.getUTCDate() !== d
  ) {
    die(`${stamp} is not a real date`);
  }
  return date;
}

const iso = (d) => d.toISOString().slice(0, 10);
const longDate = (d) =>
  `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
const shortDate = (d) => `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;

/* ------------------------------------------------------------------ inline */

function escapeHtml(text, quote = false) {
  let out = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  if (quote) out = out.replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  return out;
}

/**
 * Escape, then apply the inline subset. Order matters: references resolve
 * before bold, so a bolded link is still caught as two separate things.
 */
function inline(text, refs, seen) {
  let out = escapeHtml(text);

  out = out.replace(/\[([^\]]+)\]\[([^\]]*)\]/g, (_, label, ref) => {
    const key = (ref || label).trim().toLowerCase();
    if (!(key in refs)) {
      die(`link reference [${key}] has no definition at the foot of the file`);
    }
    seen.add(key);
    return `<a href="${escapeHtml(refs[key], true)}">${label}</a>`;
  });

  if (/\]\(https?:\/\//.test(out)) {
    die('inline link found — the brief uses reference-style links only');
  }

  // Replacement functions, not '$1': a '$&' inside the brief's own prose would
  // otherwise be read as a backreference and silently corrupt the line.
  out = out.replace(/\*\*([^*]+)\*\*/g, (_, s) => `<strong>${s}</strong>`);
  out = out.replace(/`([^`]+)`/g, (_, s) => `<code>${s}</code>`);

  if (out.includes('**')) die(`unclosed bold: ${JSON.stringify(text.trim().slice(0, 60))}`);
  return out;
}

/* ------------------------------------------------------------------- block */

const NUMBERED = /^\s*\d+[.)]\s+/;
const BULLET = /^\s*[-*+]\s+/;

/**
 * Collect one list, tight or loose.
 *
 * A blank line between numbered items is still one list. Breaking on it
 * produced a separate <ol class="points"> per item, and because the CSS resets
 * the counter on each list the talking points rendered "1. 1. 1. 1.". So a
 * blank line only ends the list if what follows is not another item.
 */
function collectList(lines, i, marker) {
  const items = [];
  while (i < lines.length) {
    const line = lines[i];
    if (marker.test(line)) {
      items.push(line.replace(marker, '').trim());
      i += 1;
    } else if (line.trim() && /^[ \t]/.test(line) && items.length) {
      items[items.length - 1] += ` ${line.trim()}`; // continuation line
      i += 1;
    } else if (!line.trim()) {
      let j = i;
      while (j < lines.length && !lines[j].trim()) j += 1;
      if (j < lines.length && marker.test(lines[j])) {
        i = j; // blank line inside the list
        continue;
      }
      break;
    } else {
      break;
    }
  }
  return [items, i];
}

function collectParagraph(lines, i) {
  const buf = [];
  while (i < lines.length) {
    const line = lines[i].replace(/\s+$/, '');
    if (!line.trim() || line.startsWith('#') || NUMBERED.test(line) || BULLET.test(line)) break;
    buf.push(line.trim());
    i += 1;
  }
  return [buf.join(' '), i];
}

function renderBlocks(lines, refs, seen, indent = '  ') {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].replace(/\s+$/, '');
    if (!line.trim()) {
      i += 1;
      continue;
    }

    if (line.startsWith('#')) {
      die(`unexpected heading inside a section: ${JSON.stringify(line)}`);
    }

    if (NUMBERED.test(line)) {
      const [items, next] = collectList(lines, i, NUMBERED);
      i = next;
      out.push(`${indent}<ol class="points">`);
      for (const it of items) out.push(`${indent}  <li>${inline(it, refs, seen)}</li>`);
      out.push(`${indent}</ol>`);
      continue;
    }

    if (BULLET.test(line)) {
      const [items, next] = collectList(lines, i, BULLET);
      i = next;
      out.push(`${indent}<ul>`);
      for (const it of items) out.push(`${indent}  <li>${inline(it, refs, seen)}</li>`);
      out.push(`${indent}</ul>`);
      continue;
    }

    const lead = line.trimStart();
    if (lead.startsWith('>') || lead.startsWith('```') || lead.startsWith('|')) {
      die(`unsupported block (quote, fence or table): ${JSON.stringify(line.trim().slice(0, 60))}`);
    }

    const [para, next] = collectParagraph(lines, i);
    i = next;
    out.push(`${indent}<p>${inline(para, refs, seen)}</p>`);
  }
  return out;
}

/* ---------------------------------------------------------------- document */

const REF_DEF = /^\[([^\]]+)\]:\s*(\S+)\s*$/;

/** Split into { title, sections, refs }. Sections are [heading|null, lines]. */
function parse(md) {
  const refs = {};
  const body = [];
  for (const line of md.split('\n')) {
    const m = REF_DEF.exec(line);
    if (m) refs[m[1].trim().toLowerCase()] = m[2];
    else body.push(line);
  }

  let title = null;
  const sections = [];
  let current = [null, []];
  for (const line of body) {
    if (line.startsWith('# ')) {
      if (title) die('more than one H1 — the brief has a single title');
      title = line.slice(2).trim();
    } else if (line.startsWith('## ')) {
      sections.push(current);
      current = [line.slice(3).trim(), []];
    } else if (line.startsWith('###')) {
      die('H3 or deeper is not supported — the brief has one heading level below the title');
    } else {
      current[1].push(line);
    }
  }
  sections.push(current);

  if (!title) die('no H1 title found');
  return { title, sections, refs };
}

/* ---------------------------------------------------------------- template */

// The template documents itself in an HTML comment that lists the placeholder
// tokens by name. A plain replace substituted the whole brief into that list as
// well, so every page carried a second, commented-out copy of itself at roughly
// twice the size. Drop the comment before substituting and it cannot recur.
const SELF_DOC = /\n?<!--(?:(?!-->)[\s\S])*?Rendered by build\.js(?:(?!-->)[\s\S])*?-->\n?/;

function applyTemplate(template, { pageTitle, title, datetime, dateline, body }) {
  let out = template.replace(SELF_DOC, '\n');

  const fields = {
    '{{PAGE_TITLE}}': escapeHtml(pageTitle),
    '{{TITLE}}': escapeHtml(title),
    '{{DATETIME}}': datetime,
    '{{DATELINE}}': dateline,
    '{{BODY}}': body,
  };
  for (const [key, value] of Object.entries(fields)) {
    out = out.split(key).join(value);
  }

  const leftover = out.match(/\{\{[A-Z_]+\}\}/g);
  if (leftover) {
    die(`template placeholder left unsubstituted: ${[...new Set(leftover)].sort().join(', ')}`);
  }
  return out;
}

/* --------------------------------------------------------------------- nav */

const EXTRA_CSS = `
nav.archive { margin-top: 3.5em; border-top: 1px solid var(--rule); padding-top: 1.25em; }
nav.archive ul { margin: 0; padding: 0; list-style: none; }
nav.archive li { margin-bottom: 0.35em; font-family: var(--sans); font-size: 0.85em; }
nav.archive li.current { color: var(--muted); }
nav.archive li.current::after { content: " — you are here"; }
nav.archive li.all { margin-top: 0.9em; }
`;

/** The footer list: the most recent few briefs, then everything. */
function navHtml(recent, current) {
  const rows = recent.map((d) => {
    const label = escapeHtml(shortDate(d));
    return d.getTime() === current.getTime()
      ? `    <li class="current">${label}</li>`
      : `    <li><a href="${iso(d)}.html">${label}</a></li>`;
  });
  rows.push('    <li class="all"><a href="archive.html">All briefs →</a></li>');
  return (
    '  <nav class="archive">\n' +
    '    <h2>Recent briefs</h2>\n' +
    '  <ul>\n' +
    rows.join('\n') +
    '\n  </ul>\n  </nav>\n'
  );
}

function withNav(pageHtml, recent, current, label) {
  if (!pageHtml.includes('</article>')) {
    die(`${label}: no </article> in the rendered page`);
  }
  if (!pageHtml.includes('</style>')) {
    die(`${label}: no </style> in the rendered page`);
  }
  return pageHtml
    .replace('</article>', `${navHtml(recent, current)}</article>`)
    .replace('</style>', `${EXTRA_CSS}</style>`);
}

/* ------------------------------------------------------------------- pages */

function renderBrief(md, stamp, template) {
  const date = parseDate(stamp);
  const { title, sections, refs } = parse(md);
  const seen = new Set();

  // The dateline carries the date, so a dated H1 sheds its date in the HTML.
  let displayTitle = title;
  if (title.includes('—')) {
    const cut = title.indexOf('—');
    const tail = title.slice(cut + 1);
    if (/\b\d{4}\b/.test(tail)) displayTitle = title.slice(0, cut).trim();
  }

  const body = [];
  for (const [heading, lines] of sections) {
    if (heading && heading.toLowerCase().startsWith('provenance')) {
      die('Provenance section in the markdown — it belongs in the run notes');
    }
    if (heading) body.push(`  <h2>${inline(heading, refs, seen)}</h2>`);
    body.push(...renderBlocks(lines, refs, seen));
  }

  const unused = Object.keys(refs).filter((k) => !seen.has(k)).sort();
  if (unused.length) die(`unused link definitions: ${unused.join(', ')}`);

  const html = applyTemplate(template, {
    pageTitle: title,
    title: displayTitle,
    datetime: stamp,
    dateline: longDate(date),
    body: body.join('\n'),
  });

  if (body.length && html.split(body[0].trim()).length - 1 !== 1) {
    die('the rendered body appears more than once — the template is substituting it somewhere besides <article>');
  }
  return { html, date, blocks: body.length, links: Object.keys(refs).length };
}

function renderArchive(days, template) {
  const rows = days.map(
    (d) => `    <li><a href="${iso(d)}.html">${escapeHtml(longDate(d))}</a></li>`,
  );
  const body = ['  <ul>', ...rows, '  </ul>'].join('\n');
  const newest = days[0];
  const oldest = days[days.length - 1];
  const span =
    days.length === 1
      ? longDate(newest)
      : `${days.length} briefs, ${shortDate(oldest)} to ${longDate(newest)}`;

  return applyTemplate(template, {
    pageTitle: 'Every Feedbox brief',
    title: 'Every brief',
    datetime: iso(newest),
    dateline: span,
    body,
  });
}

/* -------------------------------------------------------------------- main */

function parseArgs(argv) {
  const opts = { src: 'briefs', out: 'dist', recent: 7 };
  for (let i = 0; i < argv.length; i += 2) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (!flag.startsWith('--')) die(`unexpected argument ${JSON.stringify(flag)}`);
    const key = flag.slice(2);
    if (!(key in opts)) die(`unknown option ${flag}`);
    if (value === undefined) die(`${flag} needs a value`);
    opts[key] = key === 'recent' ? Number(value) : value;
  }
  if (!Number.isInteger(opts.recent) || opts.recent < 1) {
    die('--recent needs a positive whole number');
  }
  return opts;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const srcDir = resolve(ROOT, opts.src);
  const outDir = resolve(ROOT, opts.out);

  const template = readFileSync(join(ROOT, 'template.html'), 'utf8');

  let entries;
  try {
    entries = readdirSync(srcDir);
  } catch {
    die(`no ${opts.src}/ directory — the markdown briefs live there`);
  }

  const found = new Map();
  for (const name of entries.sort()) {
    const m = /^(\d{4}-\d{2}-\d{2})\.md$/.exec(name);
    if (!m) continue;
    parseDate(m[1]); // reject an impossible date now, with the filename to hand
    found.set(m[1], join(srcDir, name));
  }
  if (!found.size) {
    die(`no YYYY-MM-DD.md files in ${opts.src}/ — nothing to build`);
  }

  const stamps = [...found.keys()].sort().reverse(); // newest first
  const days = stamps.map(parseDate);
  const recent = days.slice(0, opts.recent);
  const newest = stamps[0];

  rmSync(outDir, { recursive: true, force: true });
  mkdirSync(outDir, { recursive: true });

  let blocks = 0;
  for (const stamp of stamps) {
    const md = readFileSync(found.get(stamp), 'utf8');
    let page;
    try {
      page = renderBrief(md, stamp, template);
    } catch (err) {
      if (err instanceof BuildError) die(`${stamp}.md: ${err.message}`);
      throw err;
    }
    blocks += page.blocks;
    writeFileSync(
      join(outDir, `${stamp}.html`),
      withNav(page.html, recent, page.date, stamp),
    );
  }

  // index.html is a copy of the newest day, so the two can never drift.
  writeFileSync(join(outDir, 'index.html'), readFileSync(join(outDir, `${newest}.html`)));

  writeFileSync(join(outDir, 'archive.html'), renderArchive(days, template));

  writeFileSync(
    join(outDir, '_headers'),
    '/index.html\n' +
      '  Cache-Control: public, max-age=0, must-revalidate\n' +
      '/archive.html\n' +
      '  Cache-Control: public, max-age=0, must-revalidate\n' +
      '/*.html\n' +
      '  Cache-Control: public, max-age=600\n',
  );

  console.log(`front page: ${newest} (${longDate(days[0])})`);
  console.log(
    `wrote ${opts.out}/ — ${stamps.length} dated page(s) + index.html + archive.html + _headers, ` +
      `${blocks} body blocks, footer lists ${recent.length}`,
  );

  // A missing day is worth saying out loud: it means a run produced no brief.
  const gaps = [];
  for (let n = 1; n < days.length; n += 1) {
    const want = new Date(days[0].getTime() - n * 86400000);
    if (want < days[days.length - 1]) break;
    if (!found.has(iso(want))) gaps.push(iso(want));
  }
  if (gaps.length) {
    console.log(`gaps in the archive (no brief was written for these): ${gaps.join(', ')}`);
  }
}

try {
  main();
} catch (err) {
  if (err instanceof BuildError) {
    console.error(`FAIL: ${err.message}`);
    process.exit(1);
  }
  throw err;
}
