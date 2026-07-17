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
  `{title, date, executive_summary_md, items: [...], item_extras: [(heading, body_md), ...], skipped_md, source_health_rows, other_sections: [(heading, body_md), ...]}`.
  - `date`: try filename `news-(\d{4}-\d{2}-\d{2})\.md` first, then H1
    trailing date, else `None`.
  - `items`: split `## Items` by H3. Each H3 matching
    `^(\d+)\.\s*\[([^\]]+)\]\s*(.+)$` → `{n, tags: [...], title, fields: [(label, body_md), ...], preamble_md}`.
    Non-matching H3s under Items (e.g. `### Nízkosignálové pokračovania`) →
    append to **`item_extras`** (a field on the Items section itself, NOT
    the top-level `other_sections` list — Sprint 2 renders these
    immediately after the item cards, inside the Items block, to preserve
    their actual document position; do not drop silently).
  - `source_health_rows`: parse the first markdown table found under a
    heading matching `/^source health/i` into `[{source, status_raw, status_class, note}]`,
    `status_class` in `{ok, warn, unknown}` (see PRD: `NESPUSTENÉ` and any
    unrecognized status text both map to `unknown`, not a crash).
  - `skipped_md`: body of the first H2 matching case-insensitive prefix
    `skipped` (real heading has a trailing parenthetical — prefix match, not
    exact string).
  - Any H2 not matching Executive summary / Items / Skipped-prefix / Source
    health → `other_sections` entry, original document order preserved.
- `--strict` validation: raise/exit non-zero iff no date is resolvable
  (filename **or** H1) OR no `## Items` section exists at all — this is the
  one and only strict rule (PRD.md is the source of truth for it). Everything
  else degrades.

**Tests (pytest, `tests/test_parser.py`):**
- Parse all 3 `samples/*.md` — assert item counts **3 for `-14.md`, 5 for
  `-16.md`, 5 for `-17.md`** (confirmed by direct read of the files during
  planning — do not re-derive a different number), at least one badge
  extracted per file, source-health row count matches the table row count in
  each file.
- `news-2026-07-14.md` and `-16.md`: assert the trailing
  `### Nízkosignálové pokračovania` H3 does NOT get parsed as a numbered item
  and lands in `item_extras` (not `other_sections`, not dropped) — regression
  guard for the format quirk documented in PRD.md.
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
  clickable. `item_extras` (non-numbered H3s under Items, e.g.
  `Nízkosignálové pokračovania`) render as plain sub-blocks immediately after
  the item cards, still inside the Items section — preserves their real
  document position (see Sprint 1 fix: these are NOT in `other_sections`).
- Source health: compact status table/grid — colored dot/chip per
  `status_class` (ok=green, warn=amber, unknown=gray) + source name + note +
  a small summary strip tallying rows by class (e.g. "6 OK · 2 WARN · 1 —").
  Pure HTML/CSS.
- `## Skipped...` → `<details><summary>Skipped (N)</summary>...</details>`,
  collapsed (no `open` attribute).
- Generic `other_sections` → rendered in original document order using the
  Sprint 1 markdown-subset converter, plain card, heading preserved.
- CLI: `render_news_html.py <input.md> --out <dir> [--strict]`.
  - **Archive date set (compute once, before any write):**
    `existing = {YYYY-MM-DD parsed from filenames already in <dir>/archive/*.html}`;
    `all_dates = sorted(existing | {this_report_date}, reverse=True)`. Same
    `all_dates` list drives the footer on both pages written this run — this
    is what keeps a re-run byte-identical (the union is a fixed point once
    this date's file exists).
  - Writes `<dir>/archive/<date>.html` and `<dir>/index.html`. **They are
    NOT byte-identical** — same body content, but relative links differ by
    page location: from `index.html` (dir root) archive links are
    `archive/<d>.html`; from `archive/<date>.html` other-day links are
    `<d>.html` (sibling) and the "back to latest" link is `../index.html`.
    Render the footer with a page-relative link builder, not a hardcoded
    prefix. Current date in the footer is styled active/non-linked on its
    own page.
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
- Idempotency: render the SAME input into the SAME tmp dir twice in a row
  (in-place re-run, the realistic daily-cron case) → byte-identical
  `index.html` and byte-identical `archive/<date>.html` on both runs.
- Archive footer correctness: render two different dated samples into the
  same tmp dir in sequence → the second run's `index.html` links to the
  first day's archive page via `archive/<d>.html`, and that archive page's
  own footer links back via `../index.html` (not a dead relative path).
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

## Sprint 4 — Dark mode + responsive layout (VLA-50 sub-issue, added 2026-07-17)

**Scope:** `render_news_html.py` only — `_PAGE_CSS` and `render_page()`'s
`<head>`/body markup. See PRD.md addendum criteria 9-13. No parser changes,
no CLI changes, no change to `render-daily.sh` publish semantics (F2-owned).

- Convert `_PAGE_CSS`'s `:root` palette into light-theme custom properties,
  add a parallel dark set gated by `@media (prefers-color-scheme: dark)` AND
  a `html[data-theme="dark"]` override (manual toggle wins over OS default —
  the attribute selector must have equal/higher specificity than the media
  query, so put it after the media block or make it more specific).
- Blocking inline `<script>` as the FIRST thing in `<head>` (before
  `<style>`): reads `localStorage.getItem('theme')`, falls back to
  `matchMedia('(prefers-color-scheme: dark)')` when unset, sets
  `document.documentElement.dataset.theme` synchronously — this is what
  prevents a flash of wrong theme (must run before CSS paints, so it cannot
  be deferred/async).
- Toggle button (in the footer or header, per implementer's call) with a
  second small inline `<script>` (can be deferred, end of `<body>`): on
  click, flips `data-theme`, writes the explicit choice to `localStorage`.
  Tap target ≥ 40px per side.
- Re-check every hardcoded color in `_PAGE_CSS` (badges, status dots, links,
  code) against the dark background for WCAG AA — several are already
  custom-property-driven, some (badge fixed hex fills, `<code>` if any) are
  not; convert what needs a dark-specific value.
- Responsive pass: verify 360px width has no horizontal scroll (test the
  narrowest real device class, not just the existing `640px` breakpoint
  which only handles desktop-up); check tap target sizing on toggle/links/
  footer archive nav.
- Both `index.html` and `archive/<date>.html` get this for free since both
  are produced by the same `render_page()` call (PRD criterion 12) — no
  template fork.

**Tests (pytest, extend `tests/test_render.py`):** assert toggle button
markup present, assert the blocking theme-init `<script>` appears before
`<style>` in `<head>`, assert `prefers-color-scheme` and `data-theme` both
appear in the CSS, assert self-containment regex still passes (no external
resource loads introduced), full existing suite stays green.

**Manual/visual (non-automatable, PRD criteria 10-11):** Playwright
screenshots — index light/desktop, index dark/desktop, index light/mobile
(390px), index dark/mobile (390px), one archive page dark/mobile. Spot-check
contrast via `getComputedStyle` for body text / badges / links in dark mode
(learned in Sprint 2/3: don't eyeball a screenshot alone for color claims).

**Definition of done:** `pytest tests/ -v` green. Commit:
`feat(render): dark mode + responsive layout`. Codex/GPT-5.5 review gate run
or Sonnet may take on this sprint itself and self-run the gate (task named
this session as the sprint owner, not a subagent) — CRITICAL findings fixed
before PR.

## Review gates

After PRD+PLAN authored (before Sprint 1) and after each sprint's diff: run
`codex exec` (GPT-5.5, fallback GPT-5.4) via the `gpt55-reviewer` subagent or
direct `codex exec` call. CRITICAL findings → fix and re-review before the
next sprint starts. Non-critical findings → note in commit/summary, fix if
cheap.
