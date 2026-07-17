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
- `## Skipped (...)` (`-17.md` only, top-level; real heading is
  `## Skipped (nízky signál pre náš workflow)`, trailing parenthetical
  varies) — match by case-insensitive **prefix** `skipped`, not exact string.
  Bullet list, low-signal items.
- `## Source health` — a markdown table, columns roughly Source/Status/Note.
  Status cell contains an emoji + word. Confirmed values across the 3
  samples: `✅ OK` / `OK`, `⚠️ WARN (...)` / `WARN (...)`, and `NESPUSTENÉ`
  (seen once, `-16.md` — means "did not run this cycle", a distinct neutral
  state, NOT the same as a failure). Map by keyword/emoji, default any
  unrecognized text to the same neutral class as `NESPUSTENÉ` (never crash on
  an unseen status word).
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
     table dump) — per-source indicator + the row's own note text, pure
     HTML/CSS. "+ counts" = a small summary strip above/beside the table
     tallying rows by status_class (e.g. "6 OK · 2 WARN · 1 —"), not parsing
     numbers out of note prose.
   - `## Skipped` collapsed by default via `<details><summary>`.
   - Archive page footer links to other days present in the archive dir.
   - Mobile + desktop readable. No generic AI gray-box look. (These two are
     manual/visual review criteria, checked in Sprint 3's final pass — not
     automated pytest assertions.)
4. UTF-8 / Slovak diacritics render correctly everywhere.
5. Parser degrades gracefully on missing/extra/differently-labeled sections —
   unparseable sub-blocks render as plain markdown content, never crash.
   `--strict` exits non-zero iff: (a) no date can be determined from filename
   *or* H1, OR (b) no `## Items` section is found at all. Both docs (PRD/PLAN)
   and the implementation must use exactly this one rule — no other implicit
   strict-failure conditions.
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
