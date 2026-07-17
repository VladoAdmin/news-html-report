"""Sprint 1 (VLA-53) tests: markdown parser + data model.

Scope per PLAN.md's Sprint 1 test list:
  - Parse all 3 samples/*.md -> assert item counts (3, 5, 5), at least one
    badge extracted per file, source-health row count matches the table row
    count in each file.
  - -14.md and -16.md: the trailing non-numbered
    "### Nízkosignálové pokračovania" H3 must land in item_extras (not
    other_sections, not dropped).
  - Garbage input with --strict semantics -> parser entry point raises;
    without --strict -> best-effort structure, no raise.

A handful of small additional tests cover the markdown_to_html converter and
a few other data-model details called out in PRD.md, since those are also
part of the Sprint 1 deliverable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from render_news_html import (
    Item,
    ReportData,
    SourceHealthRow,
    StrictValidationError,
    markdown_to_html,
    parse_report,
    parse_report_file,
)

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

SAMPLE_FILES = {
    "2026-07-14": SAMPLES_DIR / "news-2026-07-14.md",
    "2026-07-16": SAMPLES_DIR / "news-2026-07-16.md",
    "2026-07-17": SAMPLES_DIR / "news-2026-07-17.md",
}

# Confirmed by direct read of the sample files during PLAN.md authoring
# (independently re-verified by reading the files again before writing
# these tests) -- do not "fix" these numbers without re-reading the samples.
EXPECTED_ITEM_COUNTS = {
    "2026-07-14": 3,
    "2026-07-16": 5,
    "2026-07-17": 5,
}


def _table_row_count(text: str) -> int:
    """Independent cross-check: count data rows (excluding header +
    separator) of the markdown table under '## Source health' by scanning
    the raw file text directly, without going through the parser."""
    m = re.search(r"^## Source health.*?$\n(.*?)(?=\n## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert m, "no '## Source health' section found in sample"
    table_lines = [ln for ln in m.group(1).split("\n") if ln.strip().startswith("|")]
    return len(table_lines) - 2  # header + separator row


# ---------------------------------------------------------------------------
# Real sample parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(EXPECTED_ITEM_COUNTS))
def test_parses_sample_item_counts(key):
    report = parse_report_file(SAMPLE_FILES[key])
    assert len(report.items) == EXPECTED_ITEM_COUNTS[key]


@pytest.mark.parametrize("key", sorted(EXPECTED_ITEM_COUNTS))
def test_each_sample_has_at_least_one_badge(key):
    report = parse_report_file(SAMPLE_FILES[key])
    all_tags = [tag for item in report.items for tag in item.tags]
    assert len(all_tags) >= 1


@pytest.mark.parametrize("key", sorted(EXPECTED_ITEM_COUNTS))
def test_source_health_row_count_matches_table(key):
    path = SAMPLE_FILES[key]
    text = path.read_text(encoding="utf-8")
    report = parse_report_file(path)
    assert len(report.source_health_rows) == _table_row_count(text)


@pytest.mark.parametrize("key", ["2026-07-14", "2026-07-16"])
def test_trailing_non_numbered_h3_goes_to_item_extras_not_other_sections(key):
    report = parse_report_file(SAMPLE_FILES[key])

    extra_headings = [h for h, _ in report.item_extras]
    assert any("Nízkosignálové pokračovania" in h for h in extra_headings), extra_headings

    other_headings = [h for h, _ in report.other_sections]
    assert not any("Nízkosignálové pokračovania" in h for h in other_headings)


def test_2026_07_17_has_no_trailing_extra_h3():
    # -17.md's Items section has no non-numbered H3 -- item_extras is empty.
    report = parse_report_file(SAMPLE_FILES["2026-07-17"])
    assert report.item_extras == []


# ---------------------------------------------------------------------------
# --strict semantics on garbage input
# ---------------------------------------------------------------------------


def test_garbage_input_strict_raises():
    with pytest.raises(StrictValidationError):
        parse_report("just some text\n", filename=None, strict=True)


def test_garbage_input_non_strict_returns_best_effort_without_raising():
    report = parse_report("just some text\n", filename=None, strict=False)
    assert isinstance(report, ReportData)
    assert report.items == []
    assert report.date is None
    assert report.title == ""


def test_strict_passes_when_date_and_items_both_present():
    # Real sample: strict=True must NOT raise.
    report = parse_report_file(SAMPLE_FILES["2026-07-14"], strict=True)
    assert report.date == "2026-07-14"
    assert len(report.items) == 3


def test_strict_raises_when_items_section_missing_even_with_date():
    text = "# Some report 2026-07-14\n\n## Executive summary\n\nHello.\n"
    with pytest.raises(StrictValidationError):
        parse_report(text, filename=None, strict=True)


def test_strict_raises_when_date_missing_even_with_items():
    text = "# Some report\n\n## Items\n\n### 1. [INFO] Title\n**Čo:** body\n"
    with pytest.raises(StrictValidationError):
        parse_report(text, filename=None, strict=True)


def test_strict_passes_with_filename_date_even_without_h1_date():
    text = "# Some report\n\n## Items\n\n### 1. [INFO] Title\n**Čo:** body\n"
    report = parse_report(text, filename="news-2026-07-14.md", strict=True)
    assert report.date == "2026-07-14"


# ---------------------------------------------------------------------------
# Other data-model details (PRD format quirks)
# ---------------------------------------------------------------------------


def test_skipped_section_matched_by_case_insensitive_prefix_in_2026_07_17():
    report = parse_report_file(SAMPLE_FILES["2026-07-17"])
    assert report.skipped_md is not None
    assert "Brainless" in report.skipped_md


@pytest.mark.parametrize("key", ["2026-07-14", "2026-07-16"])
def test_skipped_absent_in_samples_without_it(key):
    report = parse_report_file(SAMPLE_FILES[key])
    assert report.skipped_md is None


def test_date_resolved_from_filename_over_h1():
    report = parse_report_file(SAMPLE_FILES["2026-07-16"])
    assert report.date == "2026-07-16"


def test_other_sections_present_in_document_order():
    report = parse_report_file(SAMPLE_FILES["2026-07-17"])
    headings = [h for h, _ in report.other_sections]
    assert headings == ["Recommended actions", "Telegram summary draft"]


def test_source_health_status_class_maps_nespustene_to_unknown():
    # -16.md's "news_items DB ingest" row has raw status "NESPUSTENÉ" --
    # must map to "unknown", not crash and not be treated as a failure.
    report = parse_report_file(SAMPLE_FILES["2026-07-16"])
    nespustene_rows = [r for r in report.source_health_rows if "NESPUSTENÉ" in r.status_raw]
    assert nespustene_rows
    assert all(r.status_class == "unknown" for r in nespustene_rows)

    classes = {r.status_class for r in report.source_health_rows}
    assert classes <= {"ok", "warn", "unknown"}


def test_item_fields_capture_multiline_body_with_nested_list():
    # -17.md item 1's "Čo to je:" field spans an intro line plus a nested
    # bullet list before the next "**Prečo nás to zaujíma:**" label starts.
    report = parse_report_file(SAMPLE_FILES["2026-07-17"])
    item1 = next(i for i in report.items if i.n == 1)
    field_labels = [label for label, _ in item1.fields]
    assert field_labels == ["Čo to je", "Prečo nás to zaujíma", "Akcia", "Zdroj"]

    co_to_je = dict(item1.fields)["Čo to je"]
    assert "Background agenti" in co_to_je
    assert "Bezpečnostná oprava" in co_to_je


def test_item_tags_split_on_slash():
    report = parse_report_file(SAMPLE_FILES["2026-07-17"])
    item5 = next(i for i in report.items if i.n == 5)
    assert item5.tags == ["PATTERN", "INFO"]


def test_item_label_varies_co_vs_co_to_je_both_supported():
    report_14 = parse_report_file(SAMPLE_FILES["2026-07-14"])
    labels_14 = {label for item in report_14.items for label, _ in item.fields}
    assert "Čo" in labels_14

    report_17 = parse_report_file(SAMPLE_FILES["2026-07-17"])
    labels_17 = {label for item in report_17.items for label, _ in item.fields}
    assert "Čo to je" in labels_17


# ---------------------------------------------------------------------------
# markdown_to_html inline converter
# ---------------------------------------------------------------------------


def test_markdown_to_html_bold_italic_code_link():
    html = markdown_to_html("**bold** and *italic* and `code` and [link](http://example.com)")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>code</code>" in html
    assert '<a href="http://example.com">link</a>' in html


def test_markdown_to_html_bullet_list():
    html = markdown_to_html("- one\n- two\n")
    assert "<ul>" in html
    assert "<li>one</li>" in html
    assert "<li>two</li>" in html


def test_markdown_to_html_numbered_list():
    html = markdown_to_html("1. first\n2. second\n")
    assert "<ol>" in html
    assert "<li>first</li>" in html
    assert "<li>second</li>" in html


def test_markdown_to_html_paragraph_then_list_without_blank_line():
    html = markdown_to_html("intro sentence:\n- item one\n- item two\n")
    assert "<p>intro sentence:</p>" in html
    assert "<ul><li>item one</li><li>item two</li></ul>" in html


def test_markdown_to_html_escapes_raw_html():
    html = markdown_to_html("a <script>alert(1)</script> & more")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_markdown_to_html_does_not_crash_on_table_like_input():
    # Tables are handled specially by Sprint 2 for Source health; the
    # generic converter must not crash on one, a plain-paragraph fallback
    # is acceptable per PLAN.md.
    html = markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert html  # no exception, non-empty output


def test_markdown_to_html_empty_input():
    assert markdown_to_html("") == ""
    assert markdown_to_html("\n\n") == ""
