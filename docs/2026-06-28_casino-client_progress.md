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
| S2  | Generate contracts-ts from API OpenAPI | passed | 1 | merged → main | 6fc8a2a (merge dee998c) | @casino/contracts; `task contracts` deterministic; tsc RED→GREEN proven |
| S3  | Money + minor-units utils | passed | 1 | merged → main | 88c11eb (merge c4c5013) | formatMinor/parseStakeToMinor; 14 tests; no float math (BigInt) |
| S4  | Transport seam: GameClient + Http + Mock | passed | 1 | merged → main | d234add (merge 295c20d) | RNG quarantined to mock/; 11 tests; 401 re-auth+retry; crash WS union mirrors ws/crash.py. CARRY-FWD→S5/S8: HttpGameClient does NOT check res.ok before res.json() and fairness() always returns {available:true} — typed bet-error/RG-block envelope won't surface; close this when wiring bet errors / RG reasons |
| S5  | App shell, lobby, auth/balance, badges | passed | 2 | merged → main | 7116dfd+fix 9b578cc (merge 948a63e) | 16-card lobby; server-only balance (SessionProvider); badges; GameRoute+test. CARRY-FWD: registry append contract is `'id': gameView(() => import('./dir/View')),` (one line) above the marker in client/src/games/registry.tsx |
| S6  | PixiStage harness + reduced-motion | passed | 1 | merged → main | f525aa0 (merge 5e40812) | jsdom WebGL-probe seam; useReducedMotion. CARRY-FWD→S25: add vi.mock('pixi.js') lifecycle test (onReady/destroy-once/unmount-before-init race) — jsdom can't exercise teardown |
| S7  | FairnessDrawer + verifier link | passed | 1 | cherry-picked → main | 1a14807 → cp 3732c25 | renders real disclosure; verifier URL is server-stamped (verifierUrl) rendered verbatim; demo/unavailable notice; 5 tests |
| S8  | Shared BetControls + ResultPanel | passed | 1 | merged → main | 69f353c (merge 4b7f2ac) | BetControls(parseStakeToMinor, integer quick-stakes, rejectionReason prop) + ResultPanel(server-verbatim, FairnessDrawer wired); 10 tests. CARRY-FWD: ResultPanel assumes Dice-shaped outcome{multiplier,payoutMinor} — generalize for slots/table later |
| S9  | Dice view (vertical slice) | passed | 3 | merged → main | 404d986+fix 21e3e04 (merge 57187c8) | reference pattern; closed S4 res.ok via typed BetRejectedError. CARRY-FWD: (a) PixiStage redraw seam = game stage captures app+ready flag, redraws in useEffect keyed on outcome, returns draw cleanup (S11–S24 contract, documented in DiceStage); (b) redraw keys on outcome VALUE — Plinko(S17)/Crash(S18)/slots must key on bet identity (betId/nonce) for repeated identical outcomes; (c) extract pure landing math + unit-test it per game; (d) generic transport-error UI state |
| S10 | REVIEW GATE (human go) | **PASSED (go given)** | 0 | — | — | 2026-06-29 human "go". Dice redesigned to Gamdom layout first; screenshots dropped in client/screenshot/ as inspiration. FairnessDrawer post-bet-only accepted for v1. |
| S11 | Limbo view | passed | 1 | casino-client/main · wt cc-main | (this commit) | reviewer PASS after adding losing-outcome test + dropping unused limboWon. Reuses Dice pattern (LimboMeter count-up, LIMBO_PREVIEW_EDGE preview seam). outcome={generated,target,won,multiplier,payoutMinor}; input={target}. |
| S12 | Pocket Dice view | passed | 1 | casino-client/main · wt cc-main | 8ecc5d0 | reviewer PASS. {target,direction}; outcome {dice,sum,target,direction,won,multiplier,payoutMinor}; UNDER 2 / OVER 12 clamped unselectable (component-tested). 28 tests. |
| S13 | Keno view | passed | 1 | casino-client/main · wt cc-main | cea6f52 | reviewer PASS. {picks,risk}; outcome {drawn,hits,picks,risk,multiplier,payoutMinor}; NO client payout table, NO pre-bet quote (server-tabled). 29 tests. |
| S14 | Roulette 0–99 view (originals.roulette) | passed | 1 | casino-client/main · wt cc-main | 6a2179b | reviewer PASS. single-colour v1: {bets:[{value,stakeMinor==stake}]}; colours UPPERCASE GREEN/RED/BLACK; outcome {result,colour,settlements,multiplier,payoutMinor}. 21 tests. |
| S15 | Mines view (stateful) | passed | 1 | casino-client/main · wt cc-main | ed03cfd | reviewer PASS. FIRST stateful: bet()→ACTIVE public_view (not a settle); roundId==betId; reveal/cashout via /action; debit@open, credit@cashout; minePositions only at terminal; geometry-only math. 12 tests. |
| S16 | HiLo view (stateful) | passed | 1 | casino-client/main · wt cc-main | 0980e82 | reviewer PASS. bet()→ACTIVE {shownRank,1.00×}; /action guess discriminated by status (loss→revealedRank); cashout@steps≥1; debit@open, credit@cashout; neutral catch on guess/cashout faults (added post-review). 8 tests. |
| S17 | Plinko view (animated drop) | passed | 1 | casino-client/main · wt cc-main | a9df5f0 | reviewer PASS. {rows∈{8,12,16},risk}; outcome {rows,risk,bin,rightBounces,path,multiplier,payoutMinor}; ball lands on SERVER bin (path↔bin invariant tested); animation keyed on betId; NO client payout table. 36 tests. |
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
- 2026-06-28: **S2 PASSED** (1 cycle, reviewer PASS). Contracts seam `@casino/contracts` from API
  OpenAPI; `task contracts` deterministic (byte-stable regen); tsc RED before generated type, GREEN
  after. Merged dee998c.
- 2026-06-28: **S3 PASSED** (1 cycle, reviewer PASS). `client/src/lib/money.ts` formatMinor +
  parseStakeToMinor; 14 tests RED→GREEN; pure BigInt/string, no float math on money. Merged c4c5013.
- 2026-06-28: **S6 PASSED** (1 cycle, reviewer PASS). `client/src/pixi/` PixiStage (v8 async init +
  clean destroy, jsdom WebGL-probe test seam) + useReducedMotion; 4 tests. Merged 5e40812.
  NON-BLOCKING follow-up → S25: jsdom can't run WebGL so app.destroy/onReady/unmount-before-init race
  are untested; add a `vi.mock('pixi.js')` lifecycle test there.
- 2026-06-28: Aggregate check on `casino-client/main` @ 5e40812: tsc clean, vitest 20/20 (3 files),
  build OK, thin-renderer grep empty. Next eligible: **S4** (Transport seam; S2+S3 satisfied) → S5 →
  {S7,S8} → S9 → **S10 HUMAN GATE**.
- 2026-06-28: **S4 PASSED** (reviewer PASS). Merged 295c20d. **S5 PASSED** (2 cycles; reviewer
  required + accepted a GameRoute test). Merged 948a63e → progress e690113.
- 2026-06-28: **⚠️ REMOTE/REBASE EVENT.** A real GitHub remote exists: `origin`
  https://github.com/mhosseinab/casino-lacta.git . While S7 ran, the **user (MH)** committed
  `Update pnpm-workspace.yaml` and ran `git pull --rebase origin casino-client/main`, pulling the
  backend's `30923e4 Add faucets, observability suite, and client scaffold` + a `master` merge
  (`663c1e9`) from origin and REPLAYING my client S1–S5 commits on top. Result: `casino-client/main`
  moved e690113 → **9e35740** (new SHAs for the S5 commits; all content preserved; my toolchain fix
  10b140f still ancestor; pnpm-workspace.yaml still has verifyDepsBeforeRun:false). **I am NOT the
  only writer to this branch** — the user/backend push to origin and rebase. Mitigation going
  forward: before each merge, re-check `casino-client/main`'s tip and rebase/cherry-pick the step
  branch onto it. Per standing rules I do NOT push and do NOT pull --rebase unless the user asks.
- 2026-06-28: **S7 PASSED** (reviewer PASS). Because cc-S7 was based on the pre-rebase line, I
  CHERRY-PICKED its one unique commit (FairnessDrawer, new files) onto the new main → **3732c25**.
  Aggregate check @ 3732c25: pnpm install OK, tsc clean, vitest **47/47 (12 files)**, build OK,
  thin-renderer grep clean (only a doc comment in AppShell.test.tsx). Next eligible: **S8** (needs
  S5,S3 ✓) → S9 (needs S6,S7,S8) → **S10 HUMAN GATE**.
- 2026-06-28: **REMOTE POLICY (user decision):** pull --rebase origin casino-client/main BEFORE each
  step; do NOT push (user handles pushes). Observed: origin/casino-client/main advanced to bfc8995
  (user pushed my S7+progress). Pull before S8 was a no-op (up to date).
- 2026-06-28: **S8 PASSED** (reviewer PASS). Merged 4b7f2ac. S1–S8 (all pre-gate foundation) complete.
  Next: **S9 Dice slice** → **S10 HUMAN REVIEW GATE** (STOP for explicit go before S11–S24).
- 2026-06-28: **S9 PASSED** (3 cycles; reviewer caught a real template defect — PixiStage drew the
  cosmetic marker only on the FIRST bet, stale on replay; fixed with a redraw seam + pure landing-math
  unit test + generic transport-error UI; also closed the S4 res.ok gap with a typed BetRejectedError).
  Merged 57187c8. Gate verify @ 57187c8: pnpm install OK, tsc clean, vitest **70/70 (16 files)**,
  build OK, thin-renderer grep clean (only a test comment), RNG quarantine = mock/ only.
- 2026-06-28: **⛔ AT S10 HUMAN REVIEW GATE — STOPPED.** Awaiting explicit human "go" before any of
  S11–S24. Presented: GameClient seam (S4), RNG quarantine to lib/transport/mock/, BetControls/
  ResultPanel (S8), PixiStage + reduced-motion (S6), FairnessDrawer (S7), Dice slice (S9), the
  thin-renderer grep, and the redraw/test pattern. Open scope decision surfaced: fairness shown
  post-bet only. Do NOT dispatch S11+ until the human approves; fold any requested pattern changes
  back into S4–S9 first.
- 2026-06-29: **S9 DICE REDESIGN (polish on the merged slice).** Reworked the Dice view to the
  Gamdom layout the human supplied (left Manual/Auto bet panel, horizontal red/green threshold
  slider with 0/25/50/75/100 ticks + draggable thumb, stats row Multiplier | Roll Over/Under(⇄) |
  Win Chance, server-roll marker). Swapped the Pixi `DiceStage` for a DOM `DiceSlider` (a draggable
  threshold IS a form control) — kept the S9 load-bearing ideas: pure unit-tested geometry
  (`winRegion`/`diceResultLanding`), reduced-motion → immediate, marker keyed on betId. Added
  `diceMath.ts` (pure preview quote; `DICE_PREVIEW_EDGE` = the single named client edge owner,
  mirrors the server 1% default; authoritative multiplier/payout still come off `bet.outcome`).
  Added a global `index.css` (the app had NO stylesheet → UA serif) = Poppins + system fallback +
  dark canvas; made the mock dice-aware (quarantine file only). Verify @ working tree: tsc clean,
  vitest **80/80 (17 files)**, biome clean, vite build OK; browser-confirmed a settled bet renders
  (roll marker + colour + ResultPanel + balance re-fetch). Thin-renderer boundary held.
- 2026-06-29: **✅ S10 GATE CLEARED — human "go" given** ("commit then go next step"; human also
  dropped Gamdom reference screenshots in `client/screenshot/` as visual inspiration for the game
  views). Proceeding to the post-gate Originals fan-out starting with **S11 Limbo**.
- 2026-06-29: **S11 PASSED** (1 review cycle; frontend-renderer-reviewer). Limbo view on the Dice
  reference pattern: `limboMath.ts` (preview quote; `LIMBO_PREVIEW_EDGE`=1% = win-chance/profit only;
  authoritative multiplier/payout off `bet.outcome`), `LimboMeter.tsx` (cosmetic count-up to the
  server `generated`, pure `limboLanding`, reduced-motion immediate, keyed on betId), `LimboView.tsx`
  (sends `input:{target}`, re-fetches balance, typed rejection + neutral transport-fault). Registered
  `originals.limbo`; mock `fakeLimbo` (quarantine) for demo. Verified engine outcome keys against
  engine/games/limbo.py + bet_loop (generated/target/won + multiplier + payoutMinor). Reviewer
  CHANGES_REQUESTED → added a LOSING-result test (0×/0.00, no phantom credit) + removed unused
  `limboWon`; re-review PASS. Verify @ working tree: tsc clean, vitest **101/101 (20 files)**, biome
  clean, vite build OK (LimboView code-split 4.97kB), RNG quarantine clean. Browser-confirmed: win
  chance 49.50% (spec edge, not Gamdom 49%), winning bet renders count-up + "Won at 14.73×" + SETTLED
  panel + balance re-fetch 10000→10001. Next eligible (post-gate fan-out): S12 Pocket Dice, S13 Keno,
  S14 Roulette 0–99 (each appends one line to registry.tsx — serialize).
- 2026-06-29: **S12–S17 PASSED — 6-game parallel fan-out** (human: "fanout as much as you can for each
  game; screenshots are inspiration"). Six worker subagents each built ONE game under
  `client/src/games/<id>/` (TDD, local FakeGameClient, scoped verify), forbidden from the two serialize
  points; the orchestrator owned `registry.tsx` + `MockGameClient.ts` (avoids the concurrent-edit
  conflict) and the demo-mock machinery (4 instant fakes + real per-round state for Mines/HiLo so they
  are playable in the deployed demo, not dead tiles). Engine outcome KEYS were verified from engine
  source first (the `as`-cast footgun). Per-game commits: S12 8ecc5d0, S13 cea6f52, S14 6a2179b,
  S15 ed03cfd, S16 0980e82, S17 a9df5f0; wiring (registry+mock+this doc) in the following commit. All
  six routed to `frontend-renderer-reviewer` → **6× VERDICT PASS**; two non-blocking fixes applied
  (HiLo neutral catch on guess/cashout faults + test; Pocket Dice clampTarget component test). Full
  gate @ working tree: tsc clean, biome clean, **vitest 240/240 (36 files)**, vite build OK (all 6
  views code-split), RNG quarantine clean (only a pre-existing test comment). NOT pushed (user handles
  pushes). Next eligible (post-gate): S18 Crash (realtime WS), S19→S20 Slots, {S21–S24} table/video
  poker — and S25 a11y pass once all game steps land.
