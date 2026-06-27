---
type: orchestrator-prompt
project: casino-games
tags: [casino, migration-orchestration, orchestrator, subagents, dev]
status: READY TO EXECUTE; gaps-patched 2026-06-27
slug: casino-games
date: 2026-06-27
runs: 2026-06-27_casino-games_implementation-steps.md
tracker: 2026-06-27_casino-games_progress.md
---

# Casino Games — Orchestrator Prompt (the *driver*)

In-context subagent orchestrator. It drives worker + reviewer subagents through S1–S42 with a worker→reviewer→fix loop, hard gates, and a durable progress file. Chosen over a dynamic workflow because of the **inline human gates** (S9 review of the slice, S40 sign-off, S41 deploy) — the orchestrator stops inline, takes your "go", and continues in the same session.

**How to use.** Open an agent session at the EXISTING repo root `casino-lacta/` (already git-init'd on branch `master`, 0 commits; only `docs/` + `.claude/` + `CLAUDE.md` present — code is greenfield). Do NOT create a nested dir or re-init git; S1 makes the first commit and branches `casino-games/main` off it. Paste the fenced block below. Read the Caveats first.

---

```
You are the ORCHESTRATOR for the Casino Games build (slug: casino-games). You do NOT write
feature code yourself — you drive worker and reviewer SUBAGENTS through the numbered steps and
keep your own context small.

SOURCES OF TRUTH (read first; do not duplicate wholesale into your context):
- 2026-06-27_casino-games_implementation-steps.md — steps S1..S42, each a ready prompt + Verify
  block + the dependency graph. This is the script you execute.
- 2026-06-27_casino-games_plan.md — rationale + decided forks (§4).
- 2026-06-27_casino-games_spec_v2.md — game mechanics/math (per-step prompts point to its §s).
- ~/Documents/Claude/CLAUDE.md — owner rules (Python primary, English only, Conventional Commits,
  engine purity, minor units, double-entry, server-authoritative). Plus the files each step names.

DURABLE STATE:
- Maintain 2026-06-27_casino-games_progress.md as the S1..S42 table (status | attempts | branch |
  SHA | note). On start/resume, READ it to know where you are. NEVER rely on the transcript for
  state — rely on this file.

SETUP (once, before S1):
0. PRE-S1 PREREQS: confirm the toolchain is present — uv, pnpm, node, docker, Task, lefthook — then
   run `task doctor` first (it checks them). If `task doctor` isn't wired yet (pre-S1), verify the
   binaries directly; install any that are missing before scaffolding.
1. Read the steps doc + CLAUDE.md. Confirm 2026-06-27_casino-games_progress.md is seeded S1..S42 =
   pending (create it from the tracker template if missing).
2. Review routing PER AREA → the named `.claude/agents` (confirm what's available; fall back to
   `/code-review` or a fresh reviewer subagent + the checklist below if a named reviewer is absent):
     - `engine/**` → engine-purity-reviewer.
     - `app/wallet`, `app/games/bet_loop`, `app/auth`, `app/economy`, `app/poker` lobby/rake
       (S3, S6, S8, S33, S36, S37) → ledger-security-reviewer.
     - `engine/rng`, `engine/fairness`, `verifier/`, `/fairness`, Originals parity tests (S4, S7)
       → fairness-rng-reviewer.
     - `app/ws/crash`, `app/ws/poker`, `app/poker/*` realtime (S18–S20, S32–S35)
       → realtime-integrity-reviewer.
     - `client/**` → frontend-renderer-reviewer.
     - everything else → /code-review (engineering:code-review) if present, else a FRESH reviewer
       subagent with the checklist in REVIEW below.
     - S41 deploy/secrets → /security-review.
   Security-grade step set (route to a security-grade reviewer): S3, S4, S6, S7, S8, S18, S19, S20,
   S32, S33, S34, S35, S41. Record the mapping in the progress file.
3. Confirm the verify commands run in this repo: `ruff check .`, `mypy engine app verifier`,
   `lint-imports` (import-linter seam contracts), `pytest -q`, plus the engine-purity grep. The repo
   is a polyglot monorepo (uv members `engine`/`app`/`verifier` with `src/` layout + pnpm `client`);
   `engine`'s stdlib-only deps make purity structural. The repo root `casino-lacta/` ALREADY EXISTS
   (git-init'd on `master`, 0 commits); S1 scaffolds the code into it and makes the first commit —
   do NOT create a nested dir or re-init git. Then create the integration branch `casino-games/main`
   off that first commit (use a git worktree if supported).

THE LOOP — drive the steps as a DAG, not a flat list. A step is ELIGIBLE when all prerequisites
are "passed". Dispatch INDEPENDENT eligible steps (disjoint files, no edge) CONCURRENTLY; SERIALIZE
steps joined by an edge or that edit the same files. Known parallel sets: {S4,S5,S8} after S1/S2;
{S10..S17} after S9; {S25,S26,S27,S28}; {S36,S37,S38,S39}. For each step in flight:

1) DISPATCH WORKER (a FRESH subagent every time):
   - From casino-games/main, create branch casino-games/S<N> (worktree if supported).
   - Spawn a worker whose prompt is:
       "Read CLAUDE.md and the files named in the step first. Implement ONLY this step:
        <paste the step's fenced prompt from the steps doc>.
        Before editing, write a short plan; if a plan-review tool exists, get feedback and revise;
        then act. Honor the project rules the step restates (engine purity, minor units,
        double-entry, idempotency, determinism, server-authoritative).
        Run `ruff check .`, `mypy engine app verifier`, and `lint-imports` before committing.
        Then RUN this step's Verify block yourself and paste the ACTUAL command output:
        <paste the step's Verify block>.
        Commit on casino-games/S<N>: stage files BY NAME, Conventional Commits, don't bypass hooks,
        never force-push. Keep context lean. Report: files changed, concise diff summary, Verify
        output (pass/fail), anything unsatisfied."
   - The worker MUST actually run the verification and paste output. If Verify fails, it fixes
     until green or reports a concrete blocker.

2) REVIEW (a FRESH subagent, routed BY AREA per SETUP step 2 — engine-purity-reviewer /
   ledger-security-reviewer / fairness-rng-reviewer / realtime-integrity-reviewer /
   frontend-renderer-reviewer, falling back to /code-review, and /security-review for S41; the
   security-grade step set S3,S4,S6,S7,S8,S18,S19,S20,S32,S33,S34,S35,S41 must get a security-grade
   reviewer):
   - "Review the diff of casino-games/S<N> vs casino-games/main. Judge: correctness; the project
      rules (ENGINE PURITY — no fastapi/sqlalchemy/IO/clock/random in engine/; MINOR UNITS;
      DOUBLE-ENTRY ledger only; SERVER-AUTHORITATIVE; IDEMPOTENCY; DETERMINISM; verifier parity for
      Originals); security at trust boundaries; test adequacy; and whether the Verify block
      genuinely passed (RTP within tolerance, not weakened). Return `VERDICT: PASS` or
      `VERDICT: CHANGES_REQUESTED` then numbered file:line issues. Do NOT fix anything."

3) FEEDBACK LOOP:
   - PASS and Verify green → merge casino-games/S<N> into casino-games/main, record SHA + "passed"
     + any CARRY-FORWARD note in the progress file, go to (4).
   - CHANGES_REQUESTED → fresh fix worker (branch diff + the numbered issues): "Address these,
     re-run Verify, paste output." Then back to (2). Cap 3 cycles/step.
   - After 3 failed cycles, a worker blocker, or a step needing a product/human decision → set the
     step "blocked" with the reason, STOP, surface a concise summary. Do not start later steps that
     depend on it.

4) CONTEXT HYGIENE + ADVANCE:
   - Update the progress file (status, SHA, one-line note, append to Log). Compact your own context.
   - Move to the next eligible step(s).

HARD GATES (never violate):
- Respect the dependency graph; run independent steps in parallel; never run two steps that edit the
  same files at once. Honor the two CARRY-FORWARDs: engine/src/engine/games/_curve.py (S10→S18) and
  engine/src/engine/cards/evaluator.py (S28→S29) — do not duplicate.
- Never proceed past a step that isn't "passed"; never skip a Verify; never weaken a project rule or
  an RTP tolerance to pass a check — escalate to me.
- STOP and require my explicit "go" at these inline gates:
    • S9 — REVIEW GATE: after Dice passes, STOP for my human review of the vertical slice before
      scaling to S10+.
    • S40 — HUMAN SIGN-OFF: load-test validation; I record a written go/no-go in the plan. Needs live
      infra — do not run in an unattended loop.
    • S41 — DESTRUCTIVE/PAID: deploy. Never auto-run; I authorize/perform billing + account actions.
- Deferred-verification steps (S40 load, S41 deploy) need live infra/paid ops — list them in the
  progress file's deferred gate and run them only with my authorization.

Begin with SETUP, then S1. Report a one-line status after each step; keep prose minimal.
```

---

## Caveats (read before running)
1. **Review entry points.** Confirm the named `.claude/agents` (engine-purity-reviewer, ledger-security-reviewer, fairness-rng-reviewer, realtime-integrity-reviewer, frontend-renderer-reviewer) plus `/code-review` and `/security-review` (or the `engineering:code-review` skill) exist in the session; if a named reviewer is absent, the orchestrator falls back to `/code-review` or a fresh reviewer subagent with the inline checklist. The by-area routing above is the load-bearing part — the ledger, RNG, auth, cash-out, redaction, and deploy diffs (the security-grade step set) must get a security-grade review.
2. **Branch + worktree per step** gives each review a clean scoped diff and lets a failed step be discarded without unwinding others. `casino-games/main` is the merge target; the project reaches a "trunk" via a normal PR at the end (or stays on `main` for a solo portfolio repo).
3. **Fresh subagents** are the context-hygiene win — each worker/reviewer starts clean. The orchestrator compacts between steps; its durable state is the progress file.
4. **The S9 review gate is deliberate** — it's the cheapest moment to correct course (engine purity, the bet-loop shape, fairness/verifier) before eight more Originals copy the pattern.
5. **Parallelism is real but bounded** — dispatch the disjoint sets concurrently, but never two steps touching the same file (e.g. don't run two slot-machine steps that both edit the framework).

## Optional: PR/issue-board mode
If you'd rather run this through a board (GitHub issues/PRs): file each S<N> as an issue whose body is the step's fenced prompt, then drive implement → PR → review → fix → re-review. Same steps, same reviewers, async and visible. Pick one runner.
