# PRD — News HTML Report Generator

**Linear:** VLA-50 (parent), VLA-51 (this doc)
**Owner:** Klodkód (CC), for Vlado / F2 orchestrator
**Status:** Draft for review

## Problem

The nightly news pipeline produces a markdown report (`news-YYYY-MM-DD.md`).
Vlado reads it on his phone; raw markdown is not pleasant to read there
(no clickable links rendered, no visual hierarchy, source-health buried in a
table). We need a renderer that turns the markdown into a polished,
self-contained HTML page, published at a stable URL and regenerated
automatically every morning.

## Non-goals

- No hosting/cron/tailscale wiring — F2 does that after delivery.
- No network calls at render time (renderer must work offline).
- No JS framework, no build step, no CDN assets.
- No redesign of the markdown report format itself — the renderer adapts to
  the format as-is, including its inconsistencies across days (see below).

## Input format (ground truth: `samples/news-2026-07-14.md`, `-16.md`, `-17.md`)

Confirmed by reading all three real samples — they are **not** perfectly
consistent with each other, which drives the robustness requirements below:

- `# <title> YYYY-MM-DD` — H1, date at the end of the title line.
- `## Executive summary` — one or more paragraphs.
- `## Items` — H3 subsections. Numbered items match
  `### N. [TAG] Title` (TAG may contain `/`, e.g. `[PATTERN/INFO]`). TAGs seen:
  BREAKING, INFO, PATTERN, PATTERN/INFO — **must not be a hardcoded closed
  set**, unknown tags need a default style, not a crash.
  Item bodies contain bold "label" lines (`**Label:** text`), e.g. `Čo to je:`
  / `Čo:` (label text itself varies between samples — do not hardcode exact
  labels), `Prečo nás to zaujíma:`, `Akcia:`, `Zdroj:`. A label's content may
  continue across multiple lines and include a nested bullet list before the
  next label starts.
  **Not every H3 under Items is a numbered item**: `news-2026-07-14.md` and
  `-16.md` have a trailing `### Nízkosignálové pokračovania` H3 with no
  number/tag — this must degrade to a generic block, not crash the numbered-
  item parser.
- `## Skipped` (`-17.md` only, top-level) — bullet list, low-signal items.
- `## Source health` — a markdown table, columns roughly
  Source/Status/Note. Status cell contains an emoji + word
  (✅ OK / ⚠️ WARN / and unseen-but-plausible ❌/dead/NESPUSTENÉ) — match on
  emoji or keyword, not on any fixed enum image the humans might spell
  differently.
- `## Recommended actions`, `## Telegram summary draft` — present in all 3
  samples, **not mentioned in the task's format spec** — proof that "extra
  sections" is a real, not hypothetical, case. Must render, not crash.

## Acceptance criteria

1. `python3 render_news_html.py <input.md> --out <dir>` → `<dir>/index.html`
   + `<dir>/archive/YYYY-MM-DD.html`. Exit 0. Idempotent (re-run same input →
   byte-identical output). Deterministic (no wall-clock/locale/hash-seed
   dependence in output bytes).
2. `index.html` fully self-contained: inline `<style>`, system font stack, no
   `<link href="http...">`, no `<script src="http...">`, no network fetch of
   any kind. (Anchor `<a href="http...">` links in body content are
   expected/required — this rule is about *resource loads*, not content
   links.)
3. Design (per Vlado, 2026-07-17):
   - Executive summary rendered as a lead block.
   - Each item is a card with a colored severity badge.
   - Source health as a compact visual status table (not just a markdown
     table dump) — per-source indicator + counts, pure HTML/CSS.
   - `## Skipped` collapsed by default via `<details><summary>`.
   - Archive page footer links to other days present in the archive dir.
   - Mobile + desktop readable. No generic AI gray-box look.
4. UTF-8 / Slovak diacritics render correctly everywhere.
5. Parser degrades gracefully on missing/extra/differently-labeled sections —
   unparseable sub-blocks render as plain markdown content, never crash.
   `--strict` exits non-zero on genuinely unparseable input (e.g. no H1, no
   date anywhere, no `## Items`).
6. `render-daily.sh [target_dir] [source_dir=samples/]` finds the newest
   `news-YYYY-MM-DD.md` in source dir and renders it to target dir. Single
   entry point for the daily cron.
7. Pytest suite: parses all 3 real samples (item counts, badges, source-health
   rows), self-containment check, strict-mode failure on garbage input. All
   green.
8. Python 3.11 stdlib only (confirmed: `markdown` package is **not**
   installed in this environment — hand-rolled markdown-subset conversion
   required, not optional).

## Out of scope / explicitly deferred

- RSS/Atom feed, search, pagination beyond a flat archive link list.
- Dark mode toggle (a single palette that is legible in both bright outdoor
  phone use and desktop is enough; not required to detect OS theme).
