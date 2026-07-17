"""Markdown parser and data model for the daily news report.

Sprint 1 (VLA-53) scope:
  - A tiny markdown-subset -> HTML inline converter (bold, italic, inline
    code, links, bullet/numbered lists, paragraphs). Used by Sprint 2's
    HTML renderer for field bodies / generic sections.
  - A parser that turns the raw report markdown into a structured data
    model (``ReportData``).

Sprint 2a (VLA-54, renderer core only) additionally provides:
  - ``render_page``: renders one full, self-contained HTML page for a
    ``ReportData``. Pure function -- no filesystem access, no
    ``datetime.now()``, no randomness.

No CLI, no archive/index directory writing here -- that is Sprint 2b.

Python 3.11 stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Item:
    """One numbered item under ``## Items``, e.g. ``### 1. [BREAKING] Title``."""

    n: int
    tags: list[str]
    title: str
    fields: list[tuple[str, str]]
    preamble_md: str = ""


@dataclass
class SourceHealthRow:
    """One row of the ``## Source health`` table."""

    source: str
    status_raw: str
    status_class: str  # "ok" | "warn" | "unknown"
    note: str


@dataclass
class ReportData:
    title: str
    date: str | None
    executive_summary_md: str
    items: list[Item]
    item_extras: list[tuple[str, str]]
    skipped_md: str | None
    source_health_rows: list[SourceHealthRow]
    other_sections: list[tuple[str, str]]


class StrictValidationError(ValueError):
    """Raised by :func:`parse_report` in ``strict=True`` mode when neither a
    date nor a ``## Items`` section can be found (see PRD acceptance
    criterion 5 -- this is the one and only strict-failure rule)."""


# ---------------------------------------------------------------------------
# Heading / section splitting helpers
# ---------------------------------------------------------------------------

_H1_RE = re.compile(r"^#(?!#)[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_H2_RE = re.compile(r"^##(?!#)[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_H3_RE = re.compile(r"^###(?!#)[ \t]+(.+?)[ \t]*$", re.MULTILINE)

_FILENAME_DATE_RE = re.compile(r"news-(\d{4}-\d{2}-\d{2})\.md$")
_TRAILING_DATE_RE = re.compile(r"\s+(\d{4}-\d{2}-\d{2})\s*$")

_NUMBERED_ITEM_RE = re.compile(r"^(\d+)\.\s*\[([^\]]+)\]\s*(.+)$")
_FIELD_LABEL_RE = re.compile(r"^\*\*([^*]+?):\*\*[ \t]*(.*)$")


def _split_by_heading(text: str, heading_re: re.Pattern[str]) -> list[tuple[str, str]]:
    """Split ``text`` into ``(heading, body)`` pairs at every match of
    ``heading_re``, in document order. A section's body runs up to the next
    match of the *same* heading level (nested deeper headings are not
    boundaries -- e.g. H3s inside an H2's body stay in that H2's body)."""
    matches = list(heading_re.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip("\n")
        sections.append((heading, body))
    return sections


def _extract_title_and_date(text: str, filename: str | None) -> tuple[str, str | None]:
    """``date``: filename ``news-YYYY-MM-DD.md`` first, then H1 trailing
    date, else ``None``. ``title``: the H1 text with any trailing date
    stripped."""
    h1_match = _H1_RE.search(text)
    h1_text = h1_match.group(1).strip() if h1_match else ""

    title = h1_text
    h1_date = None
    trailing_match = _TRAILING_DATE_RE.search(h1_text)
    if trailing_match:
        h1_date = trailing_match.group(1)
        title = h1_text[: trailing_match.start()].strip()

    filename_date = None
    if filename:
        fm = _FILENAME_DATE_RE.search(filename)
        if fm:
            filename_date = fm.group(1)

    date = filename_date or h1_date
    return title, date


def _classify_status(status_raw: str) -> str:
    """Map a Source-health status cell to ``ok`` / ``warn`` / ``unknown``.
    Unrecognized text (e.g. ``NESPUSTENÉ``) defaults to ``unknown`` rather
    than crashing (see PRD)."""
    if "⚠" in status_raw or re.search(r"\bWARN\b", status_raw, re.IGNORECASE):
        return "warn"
    if "✅" in status_raw or re.search(r"\bOK\b", status_raw, re.IGNORECASE):
        return "ok"
    return "unknown"


def _parse_source_health_table(body: str) -> list[SourceHealthRow]:
    """Parse the first markdown table found in ``body`` into
    ``SourceHealthRow`` entries (header + separator rows excluded)."""
    lines = body.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("|"):
            start = i
            break
    if start is None:
        return []

    table_lines = []
    for ln in lines[start:]:
        if ln.strip().startswith("|"):
            table_lines.append(ln)
        else:
            break

    if len(table_lines) < 2:
        return []

    rows = []
    for ln in table_lines[2:]:  # skip header + separator
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        source = cells[0] if len(cells) > 0 else ""
        status_raw = cells[1] if len(cells) > 1 else ""
        note = cells[2] if len(cells) > 2 else ""
        rows.append(
            SourceHealthRow(
                source=source,
                status_raw=status_raw,
                status_class=_classify_status(status_raw),
                note=note,
            )
        )
    return rows


def _parse_item_fields(body: str) -> tuple[list[tuple[str, str]], str]:
    """Split an item's H3 body into ``(label, body_md)`` fields on lines
    matching ``**Label:** text``. A field's body may span multiple lines
    (including a nested bullet list) and runs until the next label line or
    end of body. Any lines before the first label become ``preamble_md``."""
    lines = body.split("\n")
    preamble_lines: list[str] = []
    fields: list[tuple[str, str]] = []
    current_label: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_label, current_lines
        if current_label is not None:
            fields.append((current_label, "\n".join(current_lines).strip("\n")))
        current_label = None
        current_lines = []

    for line in lines:
        m = _FIELD_LABEL_RE.match(line)
        if m:
            flush()
            current_label = m.group(1).strip()
            current_lines = [m.group(2)] if m.group(2) else []
        elif current_label is None:
            preamble_lines.append(line)
        else:
            current_lines.append(line)
    flush()

    preamble_md = "\n".join(preamble_lines).strip("\n")
    return fields, preamble_md


def _parse_items_section(body: str) -> tuple[list[Item], list[tuple[str, str]]]:
    """Split ``## Items`` body by H3. Numbered H3s (``N. [TAG] Title``)
    become ``Item`` entries; any other H3 (e.g. the non-numbered
    ``### Nízkosignálové pokračovania``) is a "extra" block, kept in its
    document position via ``item_extras`` -- NOT dropped, NOT folded into
    ``other_sections``."""
    items: list[Item] = []
    extras: list[tuple[str, str]] = []
    for heading, h3_body in _split_by_heading(body, _H3_RE):
        m = _NUMBERED_ITEM_RE.match(heading)
        if m:
            n = int(m.group(1))
            tags = [t.strip() for t in m.group(2).split("/") if t.strip()]
            item_title = m.group(3).strip()
            fields, preamble_md = _parse_item_fields(h3_body)
            items.append(
                Item(n=n, tags=tags, title=item_title, fields=fields, preamble_md=preamble_md)
            )
        else:
            extras.append((heading, h3_body))
    return items, extras


# ---------------------------------------------------------------------------
# Top-level parser entry points
# ---------------------------------------------------------------------------


def parse_report(text: str, filename: str | None = None, strict: bool = False) -> ReportData:
    """Parse raw report markdown ``text`` into a :class:`ReportData`.

    ``filename`` (basename or path) is used for the ``news-YYYY-MM-DD.md``
    date fallback. When ``strict`` is True, raises :class:`StrictValidationError`
    iff no date is resolvable from filename or H1, OR no ``## Items`` section
    is found at all -- the one and only strict-failure rule (PRD acceptance
    criterion 5). Everything else degrades to a best-effort structure.
    """
    title, date = _extract_title_and_date(text, filename)

    executive_summary_md = ""
    items: list[Item] = []
    item_extras: list[tuple[str, str]] = []
    skipped_md: str | None = None
    source_health_rows: list[SourceHealthRow] = []
    other_sections: list[tuple[str, str]] = []
    items_section_found = False

    for heading, body in _split_by_heading(text, _H2_RE):
        h_lower = heading.strip().lower()
        if h_lower == "executive summary":
            executive_summary_md = body
        elif h_lower == "items":
            items_section_found = True
            items, item_extras = _parse_items_section(body)
        elif h_lower.startswith("skipped"):
            if skipped_md is None:
                skipped_md = body
        elif h_lower.startswith("source health"):
            source_health_rows = _parse_source_health_table(body)
        else:
            other_sections.append((heading, body))

    if strict:
        errors = []
        if date is None:
            errors.append("no date resolvable from filename or H1")
        if not items_section_found:
            errors.append('no "## Items" section found')
        if errors:
            raise StrictValidationError("; ".join(errors))

    return ReportData(
        title=title,
        date=date,
        executive_summary_md=executive_summary_md,
        items=items,
        item_extras=item_extras,
        skipped_md=skipped_md,
        source_health_rows=source_health_rows,
        other_sections=other_sections,
    )


def parse_report_file(path: str | Path, strict: bool = False) -> ReportData:
    """Read ``path`` (UTF-8) and parse it via :func:`parse_report`."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return parse_report(text, filename=path.name, strict=strict)


# ---------------------------------------------------------------------------
# Markdown-subset -> HTML inline converter
# ---------------------------------------------------------------------------

_UL_LINE_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_OL_LINE_RE = re.compile(r"^\s*\d+\.\s+(.*)$")

_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_ITALIC_US_RE = re.compile(r"(?<!\w)_([^_]+)_(?!\w)")
_CODE_PLACEHOLDER_RE = re.compile(r"\x00CODE(\d+)\x00")


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _convert_inline(text: str) -> str:
    """Convert bold/italic/inline-code/link markdown spans in a single
    logical line of text to HTML. Input is treated as plain text (escaped
    first), so raw ``<``/``>``/``&`` in the source can never inject markup."""
    text = _escape_html(text)

    codes: list[str] = []

    def _stash_code(m: re.Match[str]) -> str:
        codes.append(m.group(1))
        return f"\x00CODE{len(codes) - 1}\x00"

    text = _CODE_RE.sub(_stash_code, text)

    def _link(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2)
        url = url.replace('"', "&quot;")
        return f'<a href="{url}">{label}</a>'

    text = _LINK_RE.sub(_link, text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_STAR_RE.sub(r"<em>\1</em>", text)
    text = _ITALIC_US_RE.sub(r"<em>\1</em>", text)

    def _restore_code(m: re.Match[str]) -> str:
        return f"<code>{codes[int(m.group(1))]}</code>"

    text = _CODE_PLACEHOLDER_RE.sub(_restore_code, text)
    return text


def markdown_to_html(md_text: str) -> str:
    """Convert a markdown subset (bold, italic, inline code, links, bullet
    lists, numbered lists, paragraphs) to HTML. No tables here -- Source
    health tables are handled specially by the Sprint 2 renderer; a table
    (or any other unrecognized block) met by this generic converter falls
    back to a plain escaped paragraph rather than crashing.
    """
    text = md_text.strip("\n")
    if not text.strip():
        return ""

    # Group consecutive lines of the same kind (bullet / numbered / plain
    # text) into blocks. A blank line always breaks the current block.
    # Unlike a naive "split on blank line" approach, this also splits a
    # block the moment a paragraph line transitions straight into a list
    # line with no blank line in between (a real pattern in the report
    # format: a field's intro sentence followed immediately by a nested
    # bullet list, see PRD).
    blocks: list[tuple[str, list[str]]] = []
    for line in text.split("\n"):
        if not line.strip():
            blocks.append(("blank", []))
            continue
        ul_m = _UL_LINE_RE.match(line)
        ol_m = _OL_LINE_RE.match(line)
        if ul_m:
            kind, content = "ul", ul_m.group(1)
        elif ol_m:
            kind, content = "ol", ol_m.group(1)
        else:
            kind, content = "text", line.strip()

        if blocks and blocks[-1][0] == kind:
            blocks[-1][1].append(content)
        else:
            blocks.append((kind, [content]))

    html_parts = []
    for kind, contents in blocks:
        if kind == "blank":
            continue
        if kind == "ul":
            items_html = "".join(f"<li>{_convert_inline(c)}</li>" for c in contents)
            html_parts.append(f"<ul>{items_html}</ul>")
        elif kind == "ol":
            items_html = "".join(f"<li>{_convert_inline(c)}</li>" for c in contents)
            html_parts.append(f"<ol>{items_html}</ol>")
        else:
            html_parts.append(f"<p>{_convert_inline(' '.join(contents))}</p>")
    return "\n".join(html_parts)


# ---------------------------------------------------------------------------
# Sprint 2a (VLA-54): HTML page renderer (core only -- no CLI, no file I/O)
# ---------------------------------------------------------------------------

# Badge color map: case-insensitive match on tag text (see PRD -- tags are
# NOT a hardcoded closed set, so this is a dict + .get() fallback, never an
# if/elif chain per unknown value).
_BADGE_CLASSES = {
    "BREAKING": "badge-breaking",
    "INFO": "badge-info",
    "PATTERN": "badge-pattern",
}
_BADGE_DEFAULT_CLASS = "badge-unknown"

# Source-health summary strip: fixed iteration order (not a dict/set
# iteration) so output is deterministic; label text per PRD is the literal
# "OK" / "WARN" / "—" (em dash), not the word "UNKNOWN".
_STATUS_ORDER = ("ok", "warn", "unknown")
_STATUS_LABELS = {"ok": "OK", "warn": "WARN", "unknown": "—"}

_PAGE_CSS = """
:root {
  color-scheme: light;
  --accent: #4f46e5;
  --accent-dark: #3730a3;
  --bg: #f7f6f2;
  --card-bg: #ffffff;
  --text: #1f2430;
  --muted: #5b6472;
  --border: #e2e0da;
  --ok: #15803d;
  --warn: #b45309;
  --unknown: #6b7280;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  line-height: 1.5;
  -webkit-text-size-adjust: 100%;
}
.container { max-width: 720px; margin: 0 auto; padding: 16px; }
h1, h2, h3 { line-height: 1.25; }
h1 { font-size: 1.6rem; margin: 0 0 4px; }
h2 { font-size: 1.25rem; margin: 32px 0 12px; color: var(--accent-dark); }
h3 { font-size: 1.05rem; margin: 0 0 8px; }
.lead {
  background: linear-gradient(135deg, var(--accent), var(--accent-dark));
  color: #fff;
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 24px;
}
.lead h1 { color: #fff; }
.report-date { opacity: 0.85; margin: 0 0 12px; font-size: 0.9rem; }
.lead-summary p { margin: 0 0 10px; }
.lead-summary p:last-child { margin-bottom: 0; }
.card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 14px;
  box-shadow: 0 1px 3px rgba(20, 20, 40, 0.06);
}
.item-card { border-left: 4px solid var(--accent); }
.badges { margin-bottom: 6px; }
.badge {
  display: inline-block;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  padding: 2px 8px;
  border-radius: 999px;
  margin-right: 6px;
  color: #fff;
}
.badge-breaking { background: #dc2626; }
.badge-info { background: #64748b; }
.badge-pattern { background: #7c3aed; }
.badge-unknown { background: #9ca3af; }
.item-title { margin: 4px 0 10px; }
.field { margin-top: 10px; }
.field-label {
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--accent-dark);
  text-transform: uppercase;
  letter-spacing: 0.02em;
  margin-bottom: 2px;
}
.field-body p { margin: 4px 0; }
.item-extra { padding: 10px 0; border-top: 1px dashed var(--border); margin-top: 10px; }
.health-summary { font-weight: 600; margin-bottom: 10px; }
.health-grid { display: flex; flex-direction: column; gap: 6px; }
.health-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
}
.status-dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}
.status-dot.ok { background: var(--ok); }
.status-dot.warn { background: var(--warn); }
.status-dot.unknown { background: var(--unknown); }
.health-source { font-weight: 600; flex: 0 0 auto; }
.health-note { color: var(--muted); font-size: 0.9rem; }
.skipped-section details {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 14px;
}
.skipped-section summary { cursor: pointer; font-weight: 600; }
.generic-section h2 { margin-top: 0; }
.site-footer {
  margin-top: 32px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  font-size: 0.85rem;
  color: var(--muted);
}
.site-footer a { color: var(--accent-dark); }
.site-footer .current { color: var(--text); }
@media (min-width: 640px) {
  .container { padding: 32px; }
  h1 { font-size: 2rem; }
  .health-row { align-items: center; }
}
""".strip("\n")


def _badge_class(tag: str) -> str:
    """Map a tag string to its badge CSS class, case-insensitively. Any tag
    not in the known set falls back to the neutral "unknown" class rather
    than raising (see PRD -- tags are not a closed set)."""
    return _BADGE_CLASSES.get(tag.upper(), _BADGE_DEFAULT_CLASS)


def _render_badges(tags: list[str]) -> str:
    """One badge chip per tag (a tag list of 2+ renders 2+ chips)."""
    return "".join(
        f'<span class="badge {_badge_class(tag)}">{_escape_html(tag)}</span>' for tag in tags
    )


def _render_item_card(item: Item) -> str:
    parts = ['<article class="card item-card">']
    parts.append(f'<div class="badges">{_render_badges(item.tags)}</div>')
    parts.append(f'<h3 class="item-title">{_escape_html(item.title)}</h3>')
    if item.preamble_md.strip():
        parts.append(f'<div class="item-preamble">{markdown_to_html(item.preamble_md)}</div>')
    for label, body_md in item.fields:
        parts.append(
            '<div class="field">'
            f'<div class="field-label">{_escape_html(label)}</div>'
            f'<div class="field-body">{markdown_to_html(body_md)}</div>'
            "</div>"
        )
    parts.append("</article>")
    return "\n".join(parts)


def _render_items_section(report: ReportData) -> str:
    """Item cards, followed by ``item_extras`` (non-numbered trailing H3s,
    e.g. "Nizkosignalove pokracovania") rendered as plain sub-blocks in
    original list order -- still inside the Items section, not a new
    top-level section (see PRD/PLAN: these are NOT ``other_sections``)."""
    parts = ['<section class="items-section">', "<h2>Items</h2>"]
    for item in report.items:
        parts.append(_render_item_card(item))
    for heading, body_md in report.item_extras:
        parts.append(
            '<div class="item-extra">'
            f"<h3>{_escape_html(heading)}</h3>"
            f"{markdown_to_html(body_md)}"
            "</div>"
        )
    parts.append("</section>")
    return "\n".join(parts)


def _render_source_health(report: ReportData) -> str:
    """Compact visual grid (not a raw markdown table dump): a colored dot
    per ``status_class`` + source + note, plus a summary strip tallying
    counts by class in the literal PRD style ``"6 OK · 2 WARN · 1 —"``,
    omitting any class whose count is 0."""
    rows = report.source_health_rows
    if not rows:
        return ""

    counts = {cls: 0 for cls in _STATUS_ORDER}
    for row in rows:
        counts[row.status_class] = counts.get(row.status_class, 0) + 1
    summary = " · ".join(
        f"{counts[cls]} {_STATUS_LABELS[cls]}" for cls in _STATUS_ORDER if counts[cls] > 0
    )

    row_html = []
    for row in rows:
        row_html.append(
            '<div class="health-row">'
            f'<span class="status-dot {row.status_class}"></span>'
            f'<span class="health-source">{_escape_html(row.source)}</span>'
            f'<span class="health-note">{_convert_inline(row.note)}</span>'
            "</div>"
        )

    return (
        '<section class="source-health">'
        "<h2>Source health</h2>"
        f'<div class="health-summary">{_escape_html(summary)}</div>'
        f'<div class="health-grid">{"".join(row_html)}</div>'
        "</section>"
    )


def _count_bullet_lines(md_text: str) -> int:
    """Count top-level bullet lines (``-``/``*`` at column 0, no leading
    indentation) -- used for the Skipped count. Indented/nested bullets
    are not top-level items and must not inflate this count."""
    return sum(1 for line in md_text.split("\n") if line.startswith(("-", "*")))


def _render_skipped(report: ReportData) -> str:
    """``<details><summary>Skipped (N)</summary>...</details>``, collapsed
    by default (no ``open`` attribute). Omitted entirely when
    ``skipped_md`` is ``None``."""
    if report.skipped_md is None:
        return ""
    n = _count_bullet_lines(report.skipped_md)
    body = markdown_to_html(report.skipped_md)
    return (
        '<section class="skipped-section">'
        f"<details><summary>Skipped ({n})</summary>{body}</details>"
        "</section>"
    )


def _render_other_sections(report: ReportData) -> str:
    """Generic cards for ``other_sections``, in original list order."""
    parts = []
    for heading, body_md in report.other_sections:
        parts.append(
            '<section class="card generic-section">'
            f"<h2>{_escape_html(heading)}</h2>"
            f"{markdown_to_html(body_md)}"
            "</section>"
        )
    return "\n".join(parts)


def _footer_dates_html(all_dates: list[str], current_date: str | None, is_archive_page: bool) -> str:
    """Build the footer's date-navigation links.

    Sorts defensively descending (newest first) rather than trusting the
    caller's ordering -- correctness here matters more than trusting input,
    and de-dupes via ``set()`` (order doesn't matter since we re-sort).
    ``current_date`` renders as active, non-linked text. Other dates link
    ``archive/{d}.html`` from the index page, or ``{d}.html`` (sibling) from
    an archive page; an archive page additionally gets a "Back to latest"
    link to ``../index.html``.
    """
    dates_sorted = sorted(set(all_dates), reverse=True)
    parts = []
    for d in dates_sorted:
        label = _escape_html(d)
        if d == current_date:
            parts.append(f'<strong class="current">{label}</strong>')
        elif is_archive_page:
            parts.append(f'<a href="{d}.html">{label}</a>')
        else:
            parts.append(f'<a href="archive/{d}.html">{label}</a>')
    links_html = " · ".join(parts)

    if is_archive_page:
        back = '<a href="../index.html">Back to latest</a>'
        return f"{back} · {links_html}" if links_html else back
    return links_html


def render_page(report: ReportData, all_dates: list[str], is_archive_page: bool) -> str:
    """Render one full, self-contained HTML page for ``report``.

    Both ``index.html`` and ``archive/<date>.html`` are produced by calling
    this SAME function with the same ``report`` and ``all_dates``, differing
    only in ``is_archive_page`` (governs the footer's relative link scheme).

    Pure function: no filesystem access, no ``datetime.now()``, no
    randomness, no unstable iteration order -- same inputs always produce
    byte-identical output (load-bearing for Sprint 2b's idempotency tests).
    """
    title_html = _escape_html(report.title or "News report")
    footer_html = f'<footer class="site-footer">{_footer_dates_html(all_dates, report.date, is_archive_page)}</footer>'

    body_sections = [
        '<header class="lead">'
        f"<h1>{title_html}</h1>"
        f'<p class="report-date">{_escape_html(report.date or "")}</p>'
        f'<div class="lead-summary">{markdown_to_html(report.executive_summary_md)}</div>'
        "</header>",
        _render_items_section(report),
        _render_source_health(report),
        _render_skipped(report),
        _render_other_sections(report),
        footer_html,
    ]
    body_html = "\n".join(part for part in body_sections if part)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title_html}</title>\n"
        f"<style>{_PAGE_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        f'<div class="container">\n{body_html}\n</div>\n'
        "</body>\n"
        "</html>\n"
    )


# ---------------------------------------------------------------------------
# Sprint 2b (VLA-54 cont.): archive discovery + CLI + atomic file writing
# ---------------------------------------------------------------------------

_ARCHIVE_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")
_UNDATED_ARCHIVE_STEM = "undated"


def discover_archive_dates(out_dir: str | Path) -> set[str]:
    """Scan ``<out_dir>/archive/*.html`` for filenames matching
    ``YYYY-MM-DD.html`` and return the set of date strings found.

    Returns an empty set if the archive directory doesn't exist yet. Matches
    on a filename regex rather than trusting directory-listing order, so a
    non-matching file (``notes.txt``, ``bad-name.html``) is ignored instead
    of misparsed.
    """
    archive_dir = Path(out_dir) / "archive"
    if not archive_dir.is_dir():
        return set()
    dates = set()
    for entry in archive_dir.iterdir():
        m = _ARCHIVE_FILENAME_RE.match(entry.name)
        if m:
            dates.add(m.group(1))
    return dates


def _write_atomic(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Writes to a temp file in ``path``'s own directory (creating that
    directory if needed), then ``os.replace()``s it onto the final name --
    so a crash mid-write never leaves a half-written file at ``path``, and
    the temp file never lingers on success or failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``render_news_html.py <input.md> --out <dir> [--strict]``.

    Parses ``<input.md>`` first; nothing is written to ``<out_dir>`` unless
    the parse succeeds (per ``--strict`` semantics -- see PRD acceptance
    criterion 5). On success, computes the archive date union ONCE and
    renders both pages from that same ``all_dates`` list (this is what keeps
    a re-run byte-identical), then writes both files atomically.
    """
    arg_parser = argparse.ArgumentParser(prog="render_news_html.py")
    arg_parser.add_argument("input", help="Path to the input news-YYYY-MM-DD.md report")
    arg_parser.add_argument("--out", required=True, help="Output directory")
    arg_parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail (exit 1) if no date or no '## Items' section can be resolved",
    )
    args = arg_parser.parse_args(argv)

    try:
        report = parse_report_file(args.input, strict=args.strict)
    except StrictValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    out_dir = Path(args.out)

    # Compute the archive date union ONCE, before any write, so both pages
    # rendered this run share the exact same all_dates list (this is the
    # fixed point that keeps an in-place re-run byte-identical).
    existing_dates = discover_archive_dates(out_dir)
    all_dates_set = set(existing_dates)
    if report.date is not None:
        all_dates_set.add(report.date)
    all_dates = sorted(all_dates_set, reverse=True)

    index_html = render_page(report, all_dates, is_archive_page=False)
    archive_html = render_page(report, all_dates, is_archive_page=True)

    # report.date is None only in non-strict mode (PRD allows it); fall back
    # to a stable placeholder filename for the archive copy in that edge
    # case only -- it deliberately never enters all_dates / footer nav.
    archive_stem = report.date if report.date is not None else _UNDATED_ARCHIVE_STEM

    _write_atomic(out_dir / "index.html", index_html)
    _write_atomic(out_dir / "archive" / f"{archive_stem}.html", archive_html)

    return 0


if __name__ == "__main__":
    sys.exit(main())
