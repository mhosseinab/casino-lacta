# Casino client & games — Orchestration Progress

**Durable state for the orchestrator.** On start/resume, READ this file to know where you are.
Source script: `2026-06-28_casino-client_implementation-steps.md` (S1..S29).
Plan: `2026-06-28_casino-client_plan.md`. Integration branch: `casino-client/main` (off
`casino-games/main`).
**Worktrees (MANDATORY):** `casino-client/main` is checked out at `.worktrees/cc-main` (or sibling
`../.casino-wt/cc-main`); every step runs in its own worktree `.worktrees/cc-S<N>` — never the shared
top-level checkout, which holds in-flight backend work.
Review routing: client/** + packages/** → `frontend-renderer-reviewer`; CI/deploy/secrets (S26–S28)
→ `/security-review` (or `ledger-security-reviewer` fallback) in addition to config sanity.

## Rules in force
- **Dedicated worktree per step** off `casino-client/main`; never edit/commit in the shared top-level
  tree (it carries `casino-games/*` WIP — committing there once captured backend WIP into a client
  commit and had to be rebuilt). Sandbox/mount: if `unlink` is blocked, rename stale `.git/**/*.lock`
  (and `MERGE_HEAD`) aside before each git op.
- Respect the dependency graph: start a step only when its prerequisites are `passed`; run independent
  steps (disjoint files, no edge) in parallel; never let two steps edit the same file concurrently —
  including `client/src/games/registry.tsx`, which every game step (S9, S11–S24) appends one line to
  (rebase the later parallel branch before merging).
- Cap 3 review cycles per step → else mark `blocked`, stop, surface a summary.
- Never weaken a client rule to pass a check (thin renderer / server-authoritative / minor units /
  no-Playwright) — escalate.
- **S10 = HUMAN REVIEW GATE** (after the Dice slice; before S11–S24). STOP, require an explicit "go".
- **S28 = PAID/LIVE DEPLOY** to Cloudflare Pages. Never auto-run; human authorizes account/billing/
  env/secrets/CORS. STOP, require an explicit "go".
- This change is purely ADDITIVE — there is NO destructive step. Rollback = don't merge / don't deploy.

## ⚠️ Deferred verification gate (run before / at the ship steps)
> Every Verify block except S28 runs fully offline (fixtures + the mock adapter — no live backend).
- **S28**: live Cloudflare Pages deploy — the Pages URL loads; in live mode a play-money bet succeeds
  end-to-end and `/auth/me` balance updates; in demo mode the banner shows; the verifier link resolves.
  Requires the human's Cloudflare account + billing + the backend CORS allowing the Pages origin. Run
  LAST, with authorization — not in the unattended loop.

## Status table

| Step | Title | Status | Attempts | Branch / worktree | Commit SHA | Note |
|------|-------|--------|----------|-------------------|-----------|------|
| S1  | Scaffold Vite+React+TS client | passed | 1 | casino-client/main · wt cc-main | 2bce179 | tsc clean · vitest 2/2 · vite build→dist (142kB/46kB gz) · biome clean (run off-mount; mount blocks installer unlink) |
| S2  | Generate contracts-ts from API OpenAPI | pending | 0 | — | — | seam; `task contracts` |
| S3  | Money + minor-units utils | pending | 0 | — | — | parallel w/ S2,S6 |
| S4  | Transport seam: GameClient + Http + Mock | pending | 0 | — | — | quarantine RNG to mock/ |
| S5  | App shell, lobby, auth/balance, badges | pending | 0 | — | — | balance from /auth/me |
| S6  | PixiStage harness + reduced-motion | pending | 0 | — | — | parallel w/ S2,S3 |
| S7  | FairnessDrawer + verifier link | pending | 0 | — | — | demo notice in mock mode |
| S8  | Shared BetControls + ResultPanel | pending | 0 | — | — | reused by all instant games |
| S9  | Dice view (vertical slice) | pending | 0 | — | — | the reference pattern |
| S10 | REVIEW GATE (human go) | pending | 0 | — | — | STOP before S11+ |
| S11 | Limbo view | pending | 0 | — | — | touches registry.tsx |
| S12 | Pocket Dice view | pending | 0 | — | — | touches registry.tsx |
| S13 | Keno view | pending | 0 | — | — | touches registry.tsx |
| S14 | Roulette 0–99 view (originals.roulette) | pending | 0 | — | — | touches registry.tsx |
| S15 | Mines view (stateful) | pending | 0 | — | — | /action + /state resume |
| S16 | HiLo view (stateful) | pending | 0 | — | — | /action + /state resume |
| S17 | Plinko view (animated drop) | pending | 0 | — | — | lands on SERVER slot |
| S18 | Crash view (WebSocket realtime) | pending | 0 | — | — | server-stamped cashout |
| S19 | Slots reel framework (data-driven) | pending | 0 | — | — | reels stop on server grid |
| S20 | Wire 3 slot machines + Taskfile | pending | 0 | — | — | depends S19 |
| S21 | Blackjack view (stateful) | pending | 0 | — | — | /action + /state |
| S22 | Baccarat view | pending | 0 | — | — | touches registry.tsx |
| S23 | European Roulette view (table.roulette) | pending | 0 | — | — | touches registry.tsx |
| S24 | Video Poker view (stateful) | pending | 0 | — | — | /action + /state |
| S25 | A11y / reduced-motion / responsive pass | pending | 0 | — | — | depends all game steps |
| S26 | CI typescript job + Taskfile test/contracts | pending | 0 | — | — | land after S9/S25 |
| S27 | Cloudflare Pages config (offline) | pending | 0 | — | — | no deploy here |
| S28 | DEPLOY to Cloudflare Pages (PAID, LAST) | pending | 0 | — | — | human go; deferred gate |
| S29 | Client README + demo GIF + docs index | pending | 0 | — | — | live URL/GIF post-S28 |

> Status values: pending | in_progress | passed | blocked. The Note carries CARRY-FORWARD constraints
> a downstream step must honor and why a blocked step is blocked.

## Dependency graph
```
P1 Foundations + slice (gated)
  S1 → {S2, S3, S6}
  {S2,S3} → S4 → S5
  {S4,S5} → S7
  {S5,S3} → S8
  {S6,S7,S8} → S9 (Dice) → S10 (HUMAN REVIEW GATE)

P2 Instant Originals (parallel after S10)  : {S11, S12, S13, S14}
P3 Stateful/animated Originals (after S10) : {S15, S16, S17}
P4 Crash realtime (after S10, S6)           : S18
P5 Slots (after S10, S6)                     : S19 → S20
P6 Table & video poker (after S10, S6)        : {S21, S22, S23, S24}
   (S9, S11..S24 each append ONE line to client/src/games/registry.tsx — serialize that file)

P7 Polish & ship
  {S9, S11..S24} → S25
  S9 → S26
  {S25, S26} → S27
  {S27 + all} → S28 (PAID deploy — human go, LAST)
  S27 → S29 (live URL + GIF filled post-S28)
```

## Log
> Append-only. One line per state change: what passed/blocked, merge SHA, what's next.
- 2026-06-28: Docs authored (plan/steps/orchestrator/progress). Committed on `casino-client/main`
  (ea79a9a). Progress seeded S1..S29 = pending.
- 2026-06-28: SETUP — created integration branch `casino-client/main` off `casino-games/main`;
  created the dedicated worktree `.worktrees/cc-main` (the shared top-level tree carries backend WIP
  and must not be used for client work).
- 2026-06-28: **S1 PASSED** (1 cycle). Clean scaffold committed `2bce179` on `casino-client/main` in
  the worktree (no backend WIP). Verify run off-mount on identical files (the sandbox mount blocks the
  installer's `unlink`): `tsc --noEmit` clean; `vitest run` 2/2; `vite build` → `dist` (142 kB / 46 kB
  gz); `biome check` clean. Next eligible: S2, S3, S6 (parallel) — each in its own worktree.
- NOTE (sandbox): the mount disallows `unlink`, so `.git` accumulated stale `*.lock` and a leftover
  `casino-client/S1` ref pointing at an abandoned polluted commit. Harmless to the branch tips; clean
  on a normal host with `git worktree prune`, `git branch -D casino-client/S1`, and
  `find .git -name '*.lock*' -delete`.
- 2026-06-28 (resume): **Mount changed** — cc-main was registered under a now-dead `/sessions/...`
  path; repo is now at `/Users/zen/workspace/casino-lacta`. REPAIRED worktree linkage by rewriting
  two pointer files to the current path: `.worktrees/cc-main/.git` →
  `gitdir: /Users/zen/workspace/casino-lacta/.git/worktrees/cc-main`, and
  `.git/worktrees/cc-main/gitdir` → `/Users/zen/workspace/casino-lacta/.worktrees/cc-main/.git`.
  On a future mount change, redo this. Active stale locks were renamed aside
  (`mv .git/<f>.lock .git/<f>.lock.aside.*`): `packed-refs.lock`, `objects/maintenance.lock`,
  `worktrees/cc-main/HEAD.lock`. ALSO: edit/commit the progress file in the cc-main worktree, NOT the
  shared top-level tree (it is detached + off-limits).
- 2026-06-28 (resume): **Toolchain fix committed `10b140f`** on `casino-client/main`. pnpm 11's
  auto-generated `allowBuilds` stub in `pnpm-workspace.yaml` was malformed (placeholder text), so
  `pnpm install` exited 1 on `ERR_PNPM_IGNORED_BUILDS`, and `verify-deps-before-run` cascaded that
  into every `pnpm exec`. Fixed with `verifyDepsBeforeRun: false` + a valid `onlyBuiltDependencies`
  allow-list. Verified on cc-main: `pnpm install` runs, and `pnpm exec` tsc/vitest/build/biome all
  green. Step worktrees branch from this commit so they inherit the fix. **Workers must `pnpm install`
  first in a cold worktree** (exec no longer auto-installs).
- 2026-06-28 (resume): Foundation steps S2/S3/S6 run **serially** with the orchestrator owning all git
  ops (worktree add/commit/merge) — the unlink-blocked mount makes concurrent `.git` plumbing writes
  the fragile point. Revisit parallelism for the post-S10 game fan-out.
