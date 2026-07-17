"""Sprint 2a (VLA-54) tests: HTML page renderer core.

Scope per PLAN.md's Sprint 2 test list (renderer-core subset only -- CLI /
file-writing / archive-directory scanning is Sprint 2b, not tested here):
  - Render all 3 samples/*.md via parse_report_file + render_page -> valid
    HTML shell, UTF-8 diacritics intact.
  - Badge color classes (known tags + unknown-tag fallback), multi-tag chips.
  - Source-health summary strip literal format ("N OK · N WARN · N —"),
    omitting zero-count classes.
  - Skipped <details><summary> with correct count, collapsed by default,
    absent when skipped_md is None.
  - item_extras rendered inside the Items section (real sample regression).
  - other_sections rendered in original document order (real sample).
  - Self-containment: no external <link>/<script src> resource loads.
  - Footer page-relative link builder (index vs archive page).
  - Determinism: identical inputs -> byte-identical output.
"""

from __future__ import annotations

import re
from pathlib import Path

from render_news_html import (
    Item,
    ReportData,
    SourceHealthRow,
    _badge_class,
    _footer_dates_html,
    parse_report_file,
    render_page,
)

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

SAMPLE_FILES = {
    "2026-07-14": SAMPLES_DIR / "news-2026-07-14.md",
    "2026-07-16": SAMPLES_DIR / "news-2026-07-16.md",
    "2026-07-17": SAMPLES_DIR / "news-2026-07-17.md",
}

# A known Slovak-diacritic substring from each sample, used to prove UTF-8
# survives the full parse -> render_page round trip.
KNOWN_DIACRITIC_SUBSTRING = {
    "2026-07-14": "predĺžený",
    "2026-07-16": "Dnešný",
    "2026-07-17": "sťažuje",
}

_EXTERNAL_RESOURCE_RE = re.compile(
    r"<link[^>]+href=[\"']https?://|<script[^>]+src=[\"'](?:https?:)?//"
)


def _render_sample(key: str, *, is_archive_page: bool = False) -> tuple[ReportData, str]:
    report = parse_report_file(SAMPLE_FILES[key])
    html = render_page(report, all_dates=[report.date], is_archive_page=is_archive_page)
    return report, html


# ---------------------------------------------------------------------------
# Real-sample rendering: HTML shell + UTF-8 round trip
# ---------------------------------------------------------------------------


def test_render_all_samples_produce_valid_html_shell_with_diacritics():
    for key in SAMPLE_FILES:
        _, html = _render_sample(key)
        assert "<html" in html
        assert "</html>" in html
        assert KNOWN_DIACRITIC_SUBSTRING[key] in html, key


def test_render_does_not_raise_on_any_sample():
    for key in SAMPLE_FILES:
        _render_sample(key)  # must not raise


# ---------------------------------------------------------------------------
# Badge color classes
# ---------------------------------------------------------------------------


def test_badge_class_known_tags():
    assert _badge_class("BREAKING") == "badge-breaking"
    assert _badge_class("INFO") == "badge-info"
    assert _badge_class("PATTERN") == "badge-pattern"


def test_badge_class_unknown_tag_falls_back_to_neutral_class():
    cls = _badge_class("WEIRD")
    assert cls == "badge-unknown"
    assert cls not in {"badge-breaking", "badge-info", "badge-pattern"}


def test_render_page_shows_distinct_badge_classes_per_tag():
    report = ReportData(
        title="Test report",
        date="2026-01-01",
        executive_summary_md="Summary.",
        items=[
            Item(n=1, tags=["BREAKING"], title="A", fields=[]),
            Item(n=2, tags=["INFO"], title="B", fields=[]),
            Item(n=3, tags=["PATTERN"], title="C", fields=[]),
            Item(n=4, tags=["WEIRD"], title="D", fields=[]),
        ],
        item_extras=[],
        skipped_md=None,
        source_health_rows=[],
        other_sections=[],
    )
    html = render_page(report, all_dates=["2026-01-01"], is_archive_page=False)
    assert 'class="badge badge-breaking"' in html
    assert 'class="badge badge-info"' in html
    assert 'class="badge badge-pattern"' in html
    assert 'class="badge badge-unknown"' in html


def test_render_page_multi_tag_item_renders_multiple_chips():
    # Real case: -17.md item 5 has tags ["PATTERN", "INFO"] (split on "/").
    report = parse_report_file(SAMPLE_FILES["2026-07-17"])
    html = render_page(report, all_dates=[report.date], is_archive_page=False)
    assert 'class="badge badge-pattern"' in html
    assert 'class="badge badge-info"' in html


# ---------------------------------------------------------------------------
# Source health summary strip
# ---------------------------------------------------------------------------


def _report_with_health_rows(rows: list[SourceHealthRow]) -> ReportData:
    return ReportData(
        title="Test",
        date="2026-01-01",
        executive_summary_md="",
        items=[],
        item_extras=[],
        skipped_md=None,
        source_health_rows=rows,
        other_sections=[],
    )


def test_source_health_summary_strip_literal_format():
    rows = [
        SourceHealthRow("A", "OK", "ok", ""),
        SourceHealthRow("B", "OK", "ok", ""),
        SourceHealthRow("C", "WARN", "warn", ""),
        SourceHealthRow("D", "NESPUSTENE", "unknown", ""),
    ]
    html = render_page(_report_with_health_rows(rows), all_dates=["2026-01-01"], is_archive_page=False)
    assert "2 OK · 1 WARN · 1 —" in html


def test_source_health_summary_strip_omits_zero_count_class():
    rows = [
        SourceHealthRow("A", "OK", "ok", ""),
        SourceHealthRow("B", "OK", "ok", ""),
    ]
    html = render_page(_report_with_health_rows(rows), all_dates=["2026-01-01"], is_archive_page=False)
    assert "WARN" not in html
    assert "2 OK" in html


# ---------------------------------------------------------------------------
# Skipped <details>/<summary>
# ---------------------------------------------------------------------------


def _report_with_skipped(skipped_md: str | None) -> ReportData:
    return ReportData(
        title="Test",
        date="2026-01-01",
        executive_summary_md="",
        items=[],
        item_extras=[],
        skipped_md=skipped_md,
        source_health_rows=[],
        other_sections=[],
    )


def test_skipped_details_collapsed_with_correct_count():
    skipped_md = "- one\n- two\n- three\n"
    html = render_page(_report_with_skipped(skipped_md), all_dates=["2026-01-01"], is_archive_page=False)
    assert "<details>" in html
    assert "<summary>Skipped (3)</summary>" in html
    details_tag = re.search(r"<details[^>]*>", html).group(0)
    assert "open" not in details_tag


def test_skipped_absent_when_none():
    html = render_page(_report_with_skipped(None), all_dates=["2026-01-01"], is_archive_page=False)
    assert "<details>" not in html
    assert "Skipped" not in html


# ---------------------------------------------------------------------------
# item_extras (real sample regression) and other_sections (order)
# ---------------------------------------------------------------------------


def test_item_extras_render_inside_items_section_real_sample():
    report = parse_report_file(SAMPLE_FILES["2026-07-14"])
    html = render_page(report, all_dates=[report.date], is_archive_page=False)
    assert "Nízkosignálové pokračovania" in html

    items_start = html.index('class="items-section"')
    extra_pos = html.index("Nízkosignálové pokračovania")
    footer_pos = html.index('class="site-footer"')
    assert items_start < extra_pos < footer_pos


def test_other_sections_render_in_original_order_real_sample():
    report = parse_report_file(SAMPLE_FILES["2026-07-17"])
    html = render_page(report, all_dates=[report.date], is_archive_page=False)
    assert "Recommended actions" in html
    assert "Telegram summary draft" in html
    assert html.index("Recommended actions") < html.index("Telegram summary draft")


# ---------------------------------------------------------------------------
# Self-containment
# ---------------------------------------------------------------------------


def test_no_external_resource_loads_in_any_rendered_sample():
    for key in SAMPLE_FILES:
        _, html = _render_sample(key)
        assert not _EXTERNAL_RESOURCE_RE.search(html), key


# ---------------------------------------------------------------------------
# Footer page-relative link builder
# ---------------------------------------------------------------------------


def test_footer_dates_html_index_page_links_into_archive():
    html = _footer_dates_html(["2026-07-17", "2026-07-16", "2026-07-14"], "2026-07-17", is_archive_page=False)
    assert 'href="archive/2026-07-16.html"' in html
    assert 'href="archive/2026-07-14.html"' in html
    assert 'href="archive/2026-07-17.html"' not in html


def test_footer_dates_html_archive_page_links_siblings_and_back_to_latest():
    html = _footer_dates_html(["2026-07-17", "2026-07-16", "2026-07-14"], "2026-07-16", is_archive_page=True)
    assert 'href="2026-07-17.html"' in html
    assert 'href="2026-07-14.html"' in html
    assert 'href="2026-07-16.html"' not in html
    assert 'href="../index.html"' in html


def test_render_page_footer_index_vs_archive_end_to_end():
    report = ReportData(
        title="T",
        date="2026-07-17",
        executive_summary_md="",
        items=[],
        item_extras=[],
        skipped_md=None,
        source_health_rows=[],
        other_sections=[],
    )
    all_dates = ["2026-07-17", "2026-07-16", "2026-07-14"]

    index_html = render_page(report, all_dates=all_dates, is_archive_page=False)
    assert 'href="archive/2026-07-16.html"' in index_html
    assert 'href="archive/2026-07-14.html"' in index_html
    assert 'href="archive/2026-07-17.html"' not in index_html

    archive_html = render_page(report, all_dates=all_dates, is_archive_page=True)
    assert 'href="2026-07-16.html"' in archive_html
    assert 'href="2026-07-14.html"' in archive_html
    assert 'href="../index.html"' in archive_html
    assert 'href="2026-07-17.html"' not in archive_html


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_render_page_is_deterministic():
    report = parse_report_file(SAMPLE_FILES["2026-07-16"])
    html_1 = render_page(report, all_dates=[report.date], is_archive_page=False)
    html_2 = render_page(report, all_dates=[report.date], is_archive_page=False)
    assert html_1 == html_2
