# Casino client & games — Orchestrator prompt (the driver)

In-context subagent orchestrator for the client build (`casino-client`). Chosen over a dynamic
workflow because the change has **inline human gates** — S10 (review gate before scale-out) and S28
(paid Cloudflare Pages deploy) — that need a human "go" mid-run in the same session, and to match the
existing kit's `2026-06-27_casino-games_orchestrator-prompt.md`. It runs fresh **worker** and
**reviewer** subagents through the steps with a worker→reviewer→fix loop, hard gates, and a durable
progress file.

**How to use:** paste the fenced block below into an agent session at the repo root
(`casino-lacta/`). It drives `2026-06-28_casino-client_implementation-steps.md` and keeps state in
`2026-06-28_casino-client_progress.md`. Read the Caveats after the block.

---

```
You are the ORCHESTRATOR for the casino client build (casino-client). You do NOT write feature code
yourself — you drive worker and reviewer SUBAGENTS through the numbered steps and keep your own
context small.

SOURCES OF TRUTH (read first; do not duplicate them wholesale into your context):
- docs/2026-06-28_casino-client_implementation-steps.md — steps S1..S29, each a ready prompt + a
  Verify block + the dependency graph. This is the script you execute.
- docs/2026-06-28_casino-client_plan.md — rationale + the decided design forks (§4).
- CLAUDE.md — the project iron rules (engine purity is NOT your concern here; the client trust
  boundary IS: thin renderer, server-authoritative, minor units, contracts seam, fairness/RG).
- .claude/skills/add-game-ui/SKILL.md — the per-view recipe workers invoke for each game.
- Plus the files each step names (engine modules, spec Appendix A, app WS/API).

DURABLE STATE (so a compaction never loses your place):
- Maintain docs/2026-06-28_casino-client_progress.md as a table of S1..S29: status
  (pending|in_progress|passed|blocked), attempts, branch, commit SHA, one-line note. On start or
  resume, READ this file to know where you are — NEVER rely on your transcript for state.

SETUP (once, before S1):
1. Read the steps doc + CLAUDE.md + the add-game-ui skill. Seed the progress file with S1..S29 =
   pending (it is pre-seeded; confirm it matches the steps doc).
2. Review routing — confirm and record:
   - ALL diffs under client/** and packages/** → the `frontend-renderer-reviewer` agent
     (.claude/agents/frontend-renderer-reviewer.md). It checks the load-bearing rule: presentation
     only, no client-side outcome/RNG/payout/balance logic, server-authoritative, minor units at
     display, cosmetic animation resolves to the server result, fairness + play-money surfaced, WS
     reconnect hygiene, and Vitest/RTL adequacy (NO Playwright).
   - CI / deploy / secrets diffs (S26 .github workflow, S27 Pages _headers/_redirects/env, S28
     deploy) → route to /security-review (or the ledger-security-reviewer as the security-grade
     fallback) for the CORS/headers/secret-handling surface, in ADDITION to a config sanity check.
   - If a reviewer is unavailable, fall back to a FRESH reviewer subagent with the checklist in the
     REVIEW step.
3. Confirm the verify toolchain runs here: `pnpm install`, `pnpm exec biome check`,
   `pnpm --filter @casino/client exec tsc --noEmit`, `pnpm --filter @casino/client exec vitest run`,
   `pnpm --filter @casino/client build`, and `task contracts`. Create the integration branch
   `casino-client/main` off the current HEAD of `casino-games/main`, and check it out in a
   **DEDICATED git worktree** (MANDATORY — never work in the shared top-level tree, which holds
   in-flight backend work). Use a sibling `../.casino-wt/cc-main` if reachable, else an in-repo,
   git-ignored `.worktrees/cc-main`. (Sandbox note: if the filesystem blocks `unlink`, clear any
   stale `.git/**/*.lock` by renaming them aside before each git op, and prefer per-step worktrees so
   indexes never collide.)

WORKTREE DISCIPLINE (applies to EVERY step — non-negotiable):
- Create a FRESH worktree per step off casino-client/main:
  `git worktree add -b casino-client/S<N> .worktrees/cc-S<N> casino-client/main`
  (or sibling `../.casino-wt/cc-S<N>`). The worker does ALL reads/edits/tests/commits inside it.
- Merge to casino-client/main FROM the cc-main worktree, then tear the step worktree down:
  `git worktree remove .worktrees/cc-S<N>` and `git branch -d casino-client/S<N>`.
- The shared top-level checkout is OFF-LIMITS for client work (it carries casino-games/* WIP — editing
  there can capture that WIP into a client commit). If a crashed step leaves a stale entry, run
  `git worktree prune`.

THE LOOP — drive the steps as a DAG, not a flat list. A step is ELIGIBLE when all its prerequisites
are "passed". Dispatch INDEPENDENT eligible steps (disjoint files, no edge) CONCURRENTLY; SERIALIZE
steps joined by an edge or that edit the same files. Known parallel sets: {S2,S3,S6} after S1;
{S11,S12,S13,S14}, {S15,S16,S17}, {S21,S22,S23,S24}, plus S18 and S19, all after S10. NOTE: every
game step (S9,S11..S24) appends ONE lazy-import line to client/src/games/registry.tsx — treat that
single file as a serialize point (rebase the later branch onto casino-client/main before merging;
never let two branches edit it concurrently without a rebase).

For each step in flight:

1) DISPATCH WORKER (a FRESH subagent every time — the main context-hygiene mechanism):
   - From casino-client/main, create branch casino-client/S<N> AND a DEDICATED worktree for it
     (MANDATORY — never the shared tree):
     `git worktree add -b casino-client/S<N> .worktrees/cc-S<N> casino-client/main`
     (or sibling `../.casino-wt/cc-S<N>`). Pass the worktree PATH to the worker.
   - Spawn a worker subagent whose prompt is:
       "You are working in a DEDICATED git worktree on branch casino-client/S<N> (path given) —
        all reads/edits/commits happen there, NEVER in the shared top-level working tree.
        Read CLAUDE.md, the add-game-ui skill, and the files named in the step first. For React /
        Vite / PixiJS v8 / Vitest / RTL API specifics use Context7 (resolve-library-id →
        query-docs) — do NOT guess framework syntax. Implement ONLY this step:
        <paste the step's prompt text from the implementation-steps doc>.
        Before editing, write a short plan; if a plan-review tool exists, get feedback and revise.
        Then act. Honor the client rules the step restates (thin renderer; minor units at display;
        contracts seam; cosmetic animation resolves to the server result; fairness + play-money
        surfaced). TDD: write the Vitest+RTL test from a REAL server-response fixture FIRST, watch it
        FAIL, then build the simplest view that passes — never add outcome logic to go green. If the
        step touches the contract, run `task contracts` and commit the generated file too.
        Then RUN this step's Verify block yourself and paste the ACTUAL command output:
        <paste the step's Verify block>.
        Commit IN THIS WORKTREE on casino-client/S<N>: stage files BY NAME (not add-all), Conventional
        Commits, don't bypass hooks, never force-push. Keep context lean.
        Report: files changed, a concise diff summary, the Verify output (pass/fail), and anything you
        could not satisfy."
   - The worker MUST actually run the verification and paste output. If Verify fails, it fixes until
     green or reports a concrete blocker.

2) REVIEW (a FRESH subagent — frontend-renderer-reviewer for client/packages diffs; +security-review
   for S26–S28):
   - Spawn the reviewer on the diff of casino-client/S<N> vs casino-client/main. It judges:
     the thin-renderer trust boundary (grep for Math.random / payout-multiplier math / local balance
     accumulation OUTSIDE client/src/lib/transport/mock/), server-authoritative rendering, minor
     units formatted only at display, cosmetic-only animations that resolve to the server result,
     generated-contracts usage (no hand-rolled shapes), fairness + play-money surfaced, WS reconnect
     hygiene (S18), Vitest/RTL adequacy with NO Playwright, and whether the Verify block genuinely
     passed. Return `VERDICT: PASS` or `VERDICT: CHANGES_REQUESTED` then numbered file:line issues.
     Do NOT fix anything.

3) FEEDBACK LOOP:
   - PASS and Verify green → from the cc-main worktree, merge casino-client/S<N> onto
     casino-client/main (rebase first if it touches registry.tsx); then TEAR DOWN the step worktree
     (`git worktree remove .worktrees/cc-S<N>` + `git branch -d casino-client/S<N>`); record SHA +
     "passed" in the progress file, go to (4).
   - CHANGES_REQUESTED → dispatch a FRESH fix worker (given the branch diff + the numbered issues):
     "Address these review issues, re-run the Verify block, paste output." Then back to (2). Cap at 3
     review cycles per step.
   - After 3 failed cycles, OR a worker blocker, OR a step needing a human/product decision → set the
     step "blocked" with the reason in the progress file, STOP, and surface a concise summary. Do not
     start later steps that depend on it.

4) CONTEXT HYGIENE + ADVANCE:
   - Update the progress file (status, SHA, one-line note, append to the Log). Carry forward any
     constraint a later step must honor (e.g. a contract field a view depends on).
   - Confirm the step's worktree is gone (`git worktree list` shows no `cc-S<N>`; `git worktree prune`
     if a crash left it stale) so worktrees don't accumulate.
   - Compact your own context (durable state is the progress file, not the transcript).
   - Move to the next eligible step(s).

HARD GATES (never violate):
- Respect the dependency graph — start a step only when its prerequisites are "passed"; run
  independent steps (disjoint files, no edge) in parallel; never run two steps that edit the same
  file (incl. registry.tsx) at once without a rebase.
- Never proceed past a step that isn't "passed"; never skip a Verify; never weaken a client rule (the
  thin-renderer boundary, minor-units, server-authoritative, no-Playwright) to pass a check —
  escalate to me instead.
- S10 is a HUMAN REVIEW GATE: after S9 (the Dice slice) passes, STOP and require my explicit "go"
  before dispatching ANY of S11–S24. Present the seam/BetControls/PixiStage/test-pattern + the
  thin-renderer grep for sign-off; fold requested changes back into S4–S9 first.
- S28 is a PAID/LIVE DEPLOY: never auto-run it. STOP and require my explicit "go"; I authorize and
  perform the Cloudflare account/billing, env, secret, and CORS actions. Verify it only with my
  authorization (it is a deferred/gated check, not part of the offline loop).
- Everything else (S1–S9, S11–S27, S29) verifies fully offline against fixtures + the mock adapter —
  no live backend needed.

Begin now with SETUP, then S1. Report a one-line status after each step; keep prose minimal.
```

---

## Caveats (read before running)

1. **Review entry points are real.** `frontend-renderer-reviewer` exists at
   `.claude/agents/frontend-renderer-reviewer.md` and is the right reviewer for every `client/`/
   `packages/` diff. For the CI/deploy/secrets surface (S26–S28) add a `/security-review` pass
   (CORS, `_headers` CSP, no committed secrets). If an agent can't be spawned, fall back to a fresh
   reviewer subagent with the checklist embedded in the REVIEW step.
2. **Dedicated worktrees are MANDATORY — never the shared tree.** `casino-client/main` is checked
   out in its own worktree (`.worktrees/cc-main`, or sibling `../.casino-wt/cc-main`) and EVERY step
   runs in its own worktree `.worktrees/cc-S<N>` off it. The shared top-level checkout holds in-flight
   backend work (`casino-games/*`); editing/committing there risks capturing that WIP into a client
   commit (it happened once and had to be rebuilt). Worktree-per-step also gives review a clean scoped
   diff and lets a failed step be discarded without unwinding others. The client reaches the trunk via
   one PR at the end. **Sandbox/mount note:** if the filesystem blocks `unlink` (rename works), a
   crashed git op can leave stale `.git/**/*.lock` and `MERGE_HEAD`; clear them by renaming aside
   before the next op, and keep each git operation small/single-purpose.
3. **`registry.tsx` is the one shared file** across the parallel game steps (S9, S11–S24). Each step
   appends a single lazy-import entry. Rebase the later parallel branch onto `casino-client/main`
   before merging so the appends stack cleanly — do not let two registry edits race.
4. **Fresh subagents are the context-hygiene win.** Each step's worker/reviewer/fixer starts clean.
   The orchestrator compacts between steps; the progress file is the durable state.
5. **Offline-verifiable by design.** Every Verify block except S28 runs without a live backend —
   tests drive from REAL server-response fixtures and the mock adapter. S28 (paid Pages deploy) is the
   only deferred/gated check; record it in the progress file's "Deferred verification gate" and run it
   last, with human authorization.
6. **Context7 for framework syntax.** Workers must pull current PixiJS v8 / React / Vite / Vitest /
   RTL / Cloudflare Pages docs via Context7 rather than guessing — training data drifts and Pixi v8
   changed the Application API.

## Optional: PR/issue-board mode

If you'd rather run this through a PR/board pipeline, file each step as a ticket whose body is the
step's fenced prompt and drive it implement → PR → `frontend-renderer-reviewer` → fix → re-review.
Same steps, same reviewer, async and visible. Pick one runner per change.
