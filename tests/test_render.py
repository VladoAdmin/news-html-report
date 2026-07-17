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
import subprocess
import sys
from pathlib import Path

from render_news_html import (
    Item,
    ReportData,
    SourceHealthRow,
    _badge_class,
    _footer_dates_html,
    _PAGE_CSS,
    discover_archive_dates,
    main,
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


def test_skipped_count_ignores_nested_bullets():
    # A nested sub-bullet under a top-level item is not itself a skipped
    # item -- must not inflate the "Skipped (N)" count.
    skipped_md = "- one\n  - nested detail\n- two\n"
    html = render_page(_report_with_skipped(skipped_md), all_dates=["2026-01-01"], is_archive_page=False)
    assert "<summary>Skipped (2)</summary>" in html


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
# Dark mode + responsive (Sprint 4, VLA-60)
# ---------------------------------------------------------------------------


def test_theme_toggle_button_present_on_index_and_archive_page():
    for is_archive in (False, True):
        _, html = _render_sample("2026-07-17", is_archive_page=is_archive)
        assert 'id="theme-toggle"' in html
        assert 'class="theme-toggle"' in html


def test_theme_init_script_runs_before_style_in_head():
    _, html = _render_sample("2026-07-17")
    head = html.split("</head>")[0]
    script_pos = head.index("<script>")
    style_pos = head.index("<style>")
    assert script_pos < style_pos


def test_theme_init_script_reads_localstorage_and_sets_data_theme_attribute():
    _, html = _render_sample("2026-07-17")
    head = html.split("</head>")[0]
    assert "localStorage.getItem('theme')" in head
    assert "setAttribute('data-theme'" in head


def test_dark_mode_css_present():
    _, html = _render_sample("2026-07-17")
    assert "prefers-color-scheme: dark" in html
    assert 'data-theme="dark"' in html


def test_toggle_persists_choice_to_localstorage_on_click():
    _, html = _render_sample("2026-07-17")
    assert "localStorage.setItem('theme'" in html


def test_badge_unknown_fill_meets_wcag_aa_contrast_with_white_text():
    # #9ca3af (the pre-dark-mode value) only has ~2.5:1 contrast with white
    # text -- fails WCAG AA (needs 4.5:1). #6b7280 (~4.8:1) is the fix;
    # regression guard so it doesn't silently drift back.
    assert ".badge-unknown { background: #6b7280; }" in _PAGE_CSS


def test_no_external_resource_loads_with_dark_mode_markup_present():
    # Same self-containment guarantee must hold after the toggle/theme
    # scripts were added -- inline scripts only, no src=.
    for key in SAMPLE_FILES:
        _, html = _render_sample(key)
        assert not _EXTERNAL_RESOURCE_RE.search(html), key
        assert "<script src=" not in html


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


# ---------------------------------------------------------------------------
# Sprint 2b (VLA-54 cont.): CLI + archive/index file-writing integration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_cli_main_renders_each_sample_end_to_end(tmp_path):
    for key, path in SAMPLE_FILES.items():
        out_dir = tmp_path / key
        exit_code = main([str(path), "--out", str(out_dir)])
        assert exit_code == 0, key

        index_path = out_dir / "index.html"
        archive_path = out_dir / "archive" / f"{key}.html"
        assert index_path.exists(), key
        assert archive_path.exists(), key

        for html in (index_path.read_text(encoding="utf-8"), archive_path.read_text(encoding="utf-8")):
            assert "<html" in html, key
            assert "</html>" in html, key
            assert KNOWN_DIACRITIC_SUBSTRING[key] in html, key


def test_cli_subprocess_true_end_to_end_matches_prd_invocation(tmp_path):
    # PRD acceptance criterion 1's exact invocation form: a real subprocess,
    # not a direct function call -- proves the command line actually works,
    # not just the Python function behind it.
    out_dir = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "render_news_html.py", str(SAMPLE_FILES["2026-07-16"]), "--out", str(out_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    archive_html = (out_dir / "archive" / "2026-07-16.html").read_text(encoding="utf-8")
    for html in (index_html, archive_html):
        assert "<html" in html
        assert "</html>" in html
        assert KNOWN_DIACRITIC_SUBSTRING["2026-07-16"] in html


def test_cli_output_is_self_contained():
    out_dir_key = "2026-07-17"

    def _run(tmp):
        exit_code = main([str(SAMPLE_FILES[out_dir_key]), "--out", str(tmp)])
        assert exit_code == 0

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        _run(tmp)
        index_html = (tmp / "index.html").read_text(encoding="utf-8")
        archive_html = (tmp / "archive" / f"{out_dir_key}.html").read_text(encoding="utf-8")
        assert not _EXTERNAL_RESOURCE_RE.search(index_html)
        assert not _EXTERNAL_RESOURCE_RE.search(archive_html)


def test_cli_rerun_same_sample_same_out_dir_is_byte_identical(tmp_path):
    out_dir = tmp_path / "out"
    path = SAMPLE_FILES["2026-07-16"]

    assert main([str(path), "--out", str(out_dir)]) == 0
    index_bytes_1 = (out_dir / "index.html").read_bytes()
    archive_bytes_1 = (out_dir / "archive" / "2026-07-16.html").read_bytes()

    assert main([str(path), "--out", str(out_dir)]) == 0
    index_bytes_2 = (out_dir / "index.html").read_bytes()
    archive_bytes_2 = (out_dir / "archive" / "2026-07-16.html").read_bytes()

    assert index_bytes_1 == index_bytes_2
    assert archive_bytes_1 == archive_bytes_2


def test_cli_two_daily_runs_produce_correct_cross_linked_archive_footer(tmp_path):
    out_dir = tmp_path / "out"

    # Day 1: simulate the first cron run.
    assert main([str(SAMPLE_FILES["2026-07-16"]), "--out", str(out_dir)]) == 0
    archive_16 = out_dir / "archive" / "2026-07-16.html"
    assert archive_16.exists()
    archive_16_bytes_after_day1 = archive_16.read_bytes()

    # Day 2: simulate the next day's cron run into the SAME out dir.
    assert main([str(SAMPLES_DIR / "news-2026-07-17.md"), "--out", str(out_dir)]) == 0

    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert 'href="archive/2026-07-16.html"' in index_html

    archive_17_html = (out_dir / "archive" / "2026-07-17.html").read_text(encoding="utf-8")
    assert 'href="../index.html"' in archive_17_html
    assert 'href="2026-07-16.html"' in archive_17_html

    # 2b never deletes/rewrites old archive pages -- day 1's file must still
    # exist, byte-identical to what day 1 wrote.
    assert archive_16.exists()
    assert archive_16.read_bytes() == archive_16_bytes_after_day1


def test_cli_strict_garbage_input_exits_nonzero_and_writes_nothing(tmp_path, capsys):
    garbage_md = tmp_path / "garbage.md"
    garbage_md.write_text("just some text\n", encoding="utf-8")
    out_dir = tmp_path / "out"  # fresh -- never used by a passing case

    exit_code = main([str(garbage_md), "--out", str(out_dir), "--strict"])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert captured.err.strip() != ""
    assert not out_dir.exists()
    assert not (out_dir / "index.html").exists()
    assert not (out_dir / "archive").exists()


def test_discover_archive_dates_matches_filenames_ignores_others(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "2026-07-14.html").write_text("x", encoding="utf-8")
    (archive_dir / "2026-07-16.html").write_text("x", encoding="utf-8")
    (archive_dir / "notes.txt").write_text("x", encoding="utf-8")
    (archive_dir / "bad-name.html").write_text("x", encoding="utf-8")

    assert discover_archive_dates(tmp_path) == {"2026-07-14", "2026-07-16"}


def test_discover_archive_dates_missing_dir_returns_empty_set(tmp_path):
    assert discover_archive_dates(tmp_path / "does-not-exist") == set()


def test_cli_run_leaves_no_stray_temp_files(tmp_path):
    out_dir = tmp_path / "out"
    assert main([str(SAMPLE_FILES["2026-07-14"]), "--out", str(out_dir)]) == 0

    all_files = [p for p in out_dir.rglob("*") if p.is_file()]
    assert all_files, "expected at least index.html + archive/<date>.html to exist"
    stray = [p for p in all_files if p.suffix == ".tmp" or p.name.startswith(".")]
    assert stray == []
