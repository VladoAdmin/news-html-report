---
name: sonnet-coding-discipline
description: >
  Execution discipline for any LLM coding agent working a job.md-style task in
  this repo (T-MAX pipeline). Defines the mandatory startup checks, step
  ordering, verification habits, commit protocol, and reading discipline that
  turn a written job into reviewable, green commits. Model-agnostic: written
  for Sonnet-class models but followable by any capable coding LLM. Invoke at
  the START of every job, before reading any product code.
---

# Coding discipline for job.md tasks in this repo

You received a job with steps, acceptance criteria, and invariants. Your output
is a branch of small green commits, each independently reviewable, plus a
final summary. This skill is the HOW: the order you do things in and the
checks you never skip. It is not a style guide — every rule here exists
because skipping it has a concrete failure mode.

## 0. Startup: verify state before touching code (first ~5 minutes)

Do these IN ORDER, before reading any product source file:

1. **Git state first.** `git status`, `git log --oneline -10`, checkout the
   base branch, pull. If the job names a branch to create and it ALREADY
   exists, do NOT force anything: run `git log --oneline main..branch` AND
   `branch..main`. Only reset/delete a branch you can prove has zero unique
   commits. If it has unique commits, stop and report — that is someone's
   work.
2. **Project memory and log.** Read `MEMORY.md` / `memory/` index and the last
   `log/` entry if they exist. They carry traps that cost hours (e.g. env
   flags a re-run silently needs, stale servers on known ports). Treat each
   memory as a claim to re-verify, not gospel.
3. **Project CLAUDE.md and the job's invariants section.** Invariants (what
   must NEVER change) constrain your design more than the task description
   does. Read them before designing, not after.
4. **First commit clock.** If the job sets a deadline for the first commit,
   plan step 1 small enough to hit it. Scope exploration accordingly.

Failure mode prevented: building on a wrong branch, losing someone's commits,
re-running a pipeline that wipes data because you didn't know its flag.

## 1. Map ONE data flow, not the repo

Before writing code, trace the single concept the job is about through every
layer it crosses — in this repo typically:

    engine/checker (Python) → DB table → SQL view (scripts/sql/ latest
    migration wins) → public wrapper view → lib/supabase/types.ts →
    server page → client component → E2E spec

Use grep on the concept's column/field name to find each hop; read only the
definition sites. When you can name every hop and where your change lands in
each, you are ready to code. If you cannot, you are not — keep tracing, still
narrowly.

Do NOT do a broad repo read "for context". The jobs forbid it and it burns
your first-commit window on knowledge you won't use.

## 2. Default step order: data → contract → UI → tests

Work in dependency direction so every commit leaves the system consistent:

1. Engine / data layer + the SQL view exposing it. Apply the migration, verify
   by querying the LIVE view, commit.
2. Types + the primary UI surface. Build + lint, commit.
3. Secondary UI surfaces (map, panels, links between surfaces). Build, commit.
4. E2E for the new behavior + full regression suite. Commit.

If the job dictates its own step order, follow it — it is usually ordered so
each commit is shippable. Never reorder to "batch the UI work"; that produces
one giant unreviewable commit.

## 3. Verification habits: every load-bearing claim gets a cheap test

Never let a step depend on an unverified assumption. The recurring checks:

- **DB truth over migration files.** Migrations in the repo may not match the
  live schema. Ask `information_schema.columns` for column lists; `SELECT`
  actual rows before designing queries against them. In this repo the RPC
  bridge is `ingest/supabase_client.py` (`query_sql` / `exec_sql`) with env
  sourced from the host `.env` — use it for one-off checks.
- **Run new builders/functions standalone before wiring them in.** A 5-line
  `python3 -c` harness against real data catches wrong output shape, encoding
  issues, and truncation bugs before they hide inside a pipeline.
- **Re-query after every write path runs.** After applying a migration or
  re-running the engine, SELECT from the public view the UI reads — not the
  base table — and eyeball the actual values.
- **Views may transform silently.** Check sanitizers, allowlists, CASE
  fallbacks in the view definition before assuming a column carries what the
  engine wrote. (Known instance: provenance source columns gated by a
  host allowlist return NULL for non-URL sources.)
- **`curl` the server before running browser tests.** A 200 on the target
  route costs one second and distinguishes "my test is broken" from "the
  server is down".
- **Verify process/system claims with a second, independent signal.** A
  `pgrep -f` can match your own command wrapper; before killing or restarting
  anything, confirm with `ps -o pid,ppid,args` and/or the actual socket
  (`/proc/net/tcp`, port in hex). Never escalate kills on a pattern match
  alone.

## 4. Commit protocol

- **Commit at layer boundaries, when the repo is green.** Green = `npm run
  build` + `npm run lint` + unit/integration tests pass at that point. Not
  after every edit; never only at the end.
- **Commit BEFORE any run whose artifacts embed the git hash.** In this repo
  `engine_version` is derived from `git rev-parse --short HEAD` and the
  runner purges other versions — running the engine on uncommitted code
  stamps data with a hash that doesn't match any commit.
- **Message format:** `feat|fix|test|docs(scope): summary` + bullet details +
  `Tasks: <ticket>`. Each commit message states what is verified, not just
  what changed.
- **Byproducts get their own commit.** Regenerated proof screenshots, seeds,
  fixtures — separate `docs(...)`/`chore(...)` commit, never folded into a
  feature commit.
- Do NOT merge or deploy unless the job says so. Leave the branch for review.

## 5. Definition of "done enough" for a step

A step is done when its acceptance criterion is OBSERVABLE, not when the code
looks right:

- Data step: the public view returns the new field with correct content.
- UI step: build + lint green AND you know which E2E will exercise it.
- Final: the E2E gate passes against a freshly built, freshly started server.

If an E2E fails, the default assumption is your code is wrong, not the test.
Fix the code (max 3 attempts, then document in `docs/ISSUES.md`). A failing
assertion on a "finished" step is the system telling you a wire isn't
connected — today's version of this is always some event handler / bridge
that bypasses the code path you extended. Trace the actual runtime path, don't
soften the assert.

## 6. Ambiguity protocol (autonomous mode)

You usually cannot ask mid-run. In order of preference:

1. **Resolve from the repo.** Most "ambiguous" terms name something concrete
   in the codebase (a spec term like "popup" may really be a legend/panel).
   Find the actual UI element or code path and map the requirement onto it.
2. **Choose the interpretation that also satisfies the stricter reading**, if
   the cost is small (e.g. "this class of findings needs X" → give ALL
   findings the cheap part of X, the named class the full part).
3. **State the mapping explicitly in the commit message and final summary**
   so the reviewer can veto it cheaply.
4. Only stop and ask when interpretations diverge destructively (schema
   deletion, data wipes, scope explosion).

Never blend two contradictory patterns; pick the newer/more-tested one and
say so.

## 7. Reading discipline

- Grep for the exact symbols the job names; read definition sites only.
- Long SQL/migration files: `sed -n 'A,Bp'` / offset+limit reads around the
  view you're changing. The latest migration that touches a view is the
  authoritative definition — find it with `grep -l`, don't read the history.
- Read a file end-to-end ONLY if you are about to edit it.
- Batch independent reads/greps in parallel; never re-read a file you just
  edited to "check" — the edit tool already errored if it failed.
- Prefer DB introspection over hunting DDL in the repo.

## 8. Deliberate NOTs (shortcuts a weaker run takes — refuse them)

- NO broad upfront analysis or whole-repo reads.
- NO hardcoding values a query can return (street names, ids, labels) —
  data-driven or not at all.
- NO user-facing strings outside the central label module
  (`lib/compliance/labels.ts` here). Legal citations only from the audited
  allowlist (`docs/legal-audit-44.md`); the citation gate must stay green.
- NO editing tests to make them pass; NO skipping tests silently — every
  pre-existing skip you encounter gets traced to its explicit flag and named
  in the summary.
- NO "improving" adjacent code, comments, or formatting you weren't asked to
  touch.
- NO claiming "done"/"green" for anything you did not run in this session.
  Report failures with output; report skips as skips.
- NO destructive commands (kill, reset --hard, DELETE, TRUNCATE) on
  pattern-matched evidence alone — verify the target first.

## 9. Finish protocol

1. Full suites: unit (pytest/vitest), build, lint, ALL E2E specs (new +
   regression) against a fresh production build.
2. Kill any server/process you started (stale servers on known ports are a
   documented trap in this repo).
3. `git status` — the tree must be clean except pre-existing untracked files;
   byproducts committed or reverted deliberately, never left dangling.
4. Append a `log/` entry: commits, key facts learned, test state, notes for
   the next session (e.g. flags a re-run needs).
5. Final summary: what was wired where (per layer), files changed, exact test
   results including skips, invariants checked, and every interpretation
   decision you made under §6.
