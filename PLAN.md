# PLAN — News HTML Report Generator

**Linear:** VLA-50 (parent), VLA-52 (this doc)
Ref: PRD.md for acceptance criteria and confirmed input-format quirks.

Each sprint below is executed as a **fresh subagent invocation** (Agent tool,
`subagent_type: sonnet-coder`) that reads this PLAN.md + PRD.md + prior git
log — not conversation history. Each sprint ends with a green test run and a
commit. Codex/GPT-5.5 review gate runs on the diff after each sprint; CRITICAL
findings block progression to the next sprint.

## Sprint 1 — Markdown parser + data model (VLA-53)

**Scope:** `render_news_html.py` (or a `newsreport/` package if that's
cleaner — sprint owner's call, keep it simple) containing:
- A tiny markdown-subset → HTML inline converter: bold, italic, inline code,
  links `[text](url)`, bullet lists, numbered lists, paragraphs. No tables
  needed here (tables are handled specially for Source health in Sprint 2,
  but the generic converter should not crash if it meets one — render it as
  a plain paragraph fallback is acceptable for genuinely generic sections).
- A parser: raw markdown text → structured dict/dataclass:
  `{title, date, executive_summary_md, items: [...], skipped_md, source_health_rows, other_sections: [(heading, body_md), ...]}`.
  - `date`: try filename `news-(\d{4}-\d{2}-\d{2})\.md` first, then H1
    trailing date, else `None`.
  - `items`: split `## Items` by H3. Each H3 matching
    `^(\d+)\.\s*\[([^\]]+)\]\s*(.+)$` → `{n, tags: [...], title, fields: [(label, body_md), ...], preamble_md}`.
    Non-matching H3s under Items → append to `other_sections` (do not drop
    silently).
  - `source_health_rows`: parse the first markdown table found under a
    heading matching `/^source health/i` into `[{source, status_raw, status_class, note}]`,
    `status_class` in `{ok, warn, dead, unknown}` derived by keyword/emoji
    match, default `unknown` (not a crash) if nothing matches.
  - Any H2 not matching Executive summary / Items / Skipped / Source health →
    `other_sections` entry, original order preserved.
- `--strict` validation: raise/exit non-zero when there is no H1 **and** no
  filename date, or no `## Items` section at all. Everything else degrades.

**Tests (pytest, `tests/test_parser.py`):**
- Parse all 3 `samples/*.md` — assert item counts (5, 3, 5 respectively —
  verify actual counts from the files, don't hardcode a guess), at least one
  badge extracted per file, source-health row count matches the table row
  count in each file.
- `news-2026-07-14.md` and `-16.md`: assert the trailing
  `### Nízkosignálové pokračovania` H3 does NOT get parsed as a numbered item
  (regression guard for the format quirk documented in PRD.md) and does not
  raise.
- Garbage input (e.g. `"just some text\n"`) with `--strict` semantics → parser
  entry point raises/signals failure; without `--strict` → returns a
  best-effort structure without raising.

**Definition of done:** `pytest tests/test_parser.py -v` green. Commit:
`feat(parser): markdown parsing + data model for news reports`.

## Sprint 2 — HTML renderer/template + CLI (VLA-54)

**Scope:** builds on Sprint 1's parser (read the actual function/dataclass
names from the Sprint 1 commit — do not re-derive from PLAN.md prose alone).
- Inline `<style>` template: system font stack
  (`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`),
  responsive via a single `max-width` container + media query for narrow
  viewports, no CDN/webfont/JS dependency.
- Lead block for executive summary.
- Item cards: severity badge (color map for BREAKING=red, INFO=neutral,
  PATTERN=purple; unknown tag → neutral gray fallback, multiple tags render
  as multiple badge chips), field list (label + rendered body), links
  clickable.
- Source health: compact status table/grid — colored dot or chip per
  `status_class` (ok=green, warn=amber, dead/unknown=gray/red) + source name
  + note. Pure HTML/CSS.
- `## Skipped` → `<details><summary>Skipped (N)</summary>...</details>`,
  collapsed (no `open` attribute).
- Generic `other_sections` → rendered in original document order using the
  Sprint 1 markdown-subset converter, plain card, heading preserved.
- CLI: `render_news_html.py <input.md> --out <dir> [--strict]`.
  - Writes `<dir>/archive/<date>.html` and `<dir>/index.html` (same rendered
    content; index is the "latest" alias). Footer on both lists archive dir
    contents (scanned at render time, sorted desc by filename), current date
    highlighted/non-linked, others linked to `archive/<date>.html`.
  - Exit 0 on success; on `--strict` parse failure, print reason to stderr,
    exit 1; no partial/half-written files on failure (write to temp, rename).
  - No wall-clock timestamps embedded in output (breaks idempotency) —
    if a "generated at" stamp is wanted, derive it from the report's own
    date, not `datetime.now()`.

**Tests (pytest, `tests/test_render.py`):**
- Render each of the 3 samples to a tmp dir → `index.html` and
  `archive/<date>.html` both exist, exit code 0, files are valid enough HTML
  (contains `<html`, `</html>`) and contain expected UTF-8 diacritics
  (e.g. a known Slovak substring from that day's file) — proves encoding is
  correct end to end.
- Self-containment check: no regex match for
  `<link[^>]+href=["']https?://` or `<script[^>]+src=["']https?://` or
  `<script[^>]+src=["']//`  anywhere in the output.
- Idempotency: render twice to two different tmp dirs from the same input →
  byte-identical `index.html`.
- `--strict` on garbage input → non-zero exit, no output files left behind.

**Definition of done:** `pytest tests/ -v` green (parser + render). Commit:
`feat(render): HTML template + CLI renderer`.

## Sprint 3 — render-daily.sh wrapper + demo output + final polish (VLA-55)

**Scope:**
- `render-daily.sh`: `./render-daily.sh <target_dir> [source_dir=samples/]`.
  Finds newest `news-YYYY-MM-DD.md` in source dir (glob + sort, not `ls -t`
  which depends on mtime — sort by the filename's date string), calls
  `python3 render_news_html.py <newest> --out <target_dir>`. Non-zero exit
  propagated. `set -euo pipefail`.
- `demo/` — commit the rendered output of `samples/news-2026-07-17.md`
  (`demo/index.html`, `demo/archive/2026-07-17.html`) so the orchestrator can
  open it without running anything.
- Final visual pass: actually open `demo/index.html` in a way that can be
  eyeballed (e.g. via a quick local static check / screenshot if tooling
  allows) — confirm no generic-gray-box look, badges are colored, source
  health reads as a table not a wall of text, Skipped is collapsed.
- Full `pytest tests/ -v` green, `bash render-daily.sh <tmp> samples/`
  smoke-tested manually.

**Definition of done:** commit `feat(demo): render-daily wrapper + committed demo output`.
Final summary written by the orchestrating session (not this subagent).

## Review gates

After PRD+PLAN authored (before Sprint 1) and after each sprint's diff: run
`codex exec` (GPT-5.5, fallback GPT-5.4) via the `gpt55-reviewer` subagent or
direct `codex exec` call. CRITICAL findings → fix and re-review before the
next sprint starts. Non-critical findings → note in commit/summary, fix if
cheap.
