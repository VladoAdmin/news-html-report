"""Markdown parser and data model for the daily news report.

Sprint 1 (VLA-53) scope only:
  - A tiny markdown-subset -> HTML inline converter (bold, italic, inline
    code, links, bullet/numbered lists, paragraphs). Used by Sprint 2's
    HTML renderer for field bodies / generic sections.
  - A parser that turns the raw report markdown into a structured data
    model (``ReportData``).

No HTML page template, no CLI, no archive/index writing here -- that is
Sprint 2 (VLA-54).

Python 3.11 stdlib only.
"""

from __future__ import annotations

import re
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
