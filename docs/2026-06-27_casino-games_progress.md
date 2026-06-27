---
type: progress
project: casino-games
tags: [casino, migration-orchestration, progress-tracker, dev]
status: NOT STARTED; gaps-patched 2026-06-27
slug: casino-games
date: 2026-06-27
runs: 2026-06-27_casino-games_implementation-steps.md
---

# Casino Games — Orchestration Progress

**Durable state for the orchestrator.** On start/resume, READ this file to know where you are.
Source script: `2026-06-27_casino-games_implementation-steps.md` (S1..S42).
Repo root: `casino-lacta/` (already exists, git-init'd on branch `master`, 0 commits) · Worktree: _TBD (created at SETUP)_ · Integration branch: `casino-games/main` (created off the first commit at S1)
Review routing (BY AREA → the named `.claude/agents`, falling back to `/code-review`):
| Area / step set | Reviewer |
|---|---|
| `engine/**` | `engine-purity-reviewer` |
| `app/wallet`, `app/games/bet_loop`, `app/auth`, `app/economy`, `app/poker` lobby/rake (S3, S6, S8, S33, S36, S37) | `ledger-security-reviewer` |
| `engine/rng`, `engine/fairness`, `verifier/`, `/fairness`, Originals parity tests (S4, S7) | `fairness-rng-reviewer` |
| `app/ws/crash`, `app/ws/poker`, `app/poker/*` realtime (S18–S20, S32–S35) | `realtime-integrity-reviewer` |
| `client/**` | `frontend-renderer-reviewer` |
| everything else | `/code-review` |
| S41 deploy/secrets | `/security-review` |

Security-grade step set (security-grade review required): **S3, S4, S6, S7, S8, S18, S19, S20, S32, S33, S34, S35, S41**. Fallback when a named reviewer is absent: a fresh reviewer subagent + the checklist in the orchestrator REVIEW step.

## Rules in force
- Respect the dependency graph: start a step only when prerequisites are `passed`; run independent steps (disjoint files, no edge) in parallel; never let two steps edit the same file concurrently.
- Honor CARRY-FORWARDs: `engine/src/engine/games/_curve.py` (S10 → reused by S18) is a HARD DAG edge — S18 cannot start until S10 is `passed`. `engine/src/engine/cards/evaluator.py` (S28 5-card core → extended to best-5-of-7 by S29) is a SOFT, shared-file constraint: if both S28 and S29 are in scope, SERIALIZE them (never run concurrently — same file); whichever runs first creates the evaluator, the other extends it (never duplicate).
- Cap 3 review cycles/step → else mark `blocked`, stop, surface a summary.
- Never weaken a project rule or RTP tolerance to pass a check — escalate.
- **Inline gates — require explicit "go":** S9 = REVIEW GATE (human review of the Dice slice before scaling); S40 = HUMAN SIGN-OFF (load test); S41 = DESTRUCTIVE/PAID (deploy).
- Never auto-run paid/live operations (deploy S41, any production action, secret handling).

## ⚠️ Deferred verification gate (needs live infra / paid — run with authorization)
- **S40:** load test (Crash N-client + busy poker table) under `docker compose`; assert ledger reconciles to the cent + no double-credit + p95 latency. Proves it WORKS, not just compiles.
- **S41:** deploy to Fly/Render + managed Postgres/Redis + static client; live `/health`, a real play-money bet, public verifier. Paid/live — human-authorized.
- Note: S3 concurrency, S19/S20 Crash, and S32/S34 poker tests need Postgres+Redis via `docker compose up` locally — acceptable inside the loop if compose is available; otherwise defer to the S40 window.

## Status table

| Step | Title | Status | Attempts | Branch | Commit SHA | Note |
|------|-------|--------|----------|--------|-----------|------|
| S1 | Scaffold repo + tooling + CI + health | in_progress | 1 | casino-games/main | — | first commit lands here; lefthook = pnpm devDep |
| S2 | Data model + Alembic migrations | pending | 0 | — | — | |
| S3 | Double-entry ledger service | pending | 0 | — | — | security review |
| S4 | RNG + provably-fair module (pure) | pending | 0 | — | — | security review |
| S5 | Engine contracts + money + registry | pending | 0 | — | — | |
| S6 | Shared bet loop + game API + RG hook | pending | 0 | — | — | ledger review (ledger-security-reviewer) |
| S7 | Verifier + RTP harness + CI gate | pending | 0 | — | — | fairness review (fairness-rng-reviewer) |
| S8 | Auth (play-money sessions) | pending | 0 | — | — | security review |
| S9 | Dice (vertical slice) | pending | 0 | — | — | ⛔ REVIEW GATE after pass |
| S10 | Limbo (+ _curve.py) | pending | 0 | — | — | CF: _curve.py → S18 |
| S11 | Pocket Dice | pending | 0 | — | — | |
| S12 | Mines (stateful) | pending | 0 | — | — | |
| S13 | Plinko (tuned table) | pending | 0 | — | — | |
| S14 | HiLo (stateful) | pending | 0 | — | — | |
| S15 | Keno (tuned table) | pending | 0 | — | — | |
| S16 | Roulette 0–99 | pending | 0 | — | — | |
| S17 | Optional extensions | pending | 0 | — | — | OPTIONAL — skip if descoping |
| S18 | Crash round actor + WS | pending | 0 | — | — | reuses _curve.py (S10) |
| S19 | Crash betting + cash-out | pending | 0 | — | — | security review |
| S20 | Crash recovery + latency-fairness | pending | 0 | — | — | security review |
| S21 | Slots framework engine | pending | 0 | — | — | |
| S22 | Slot feature modules | pending | 0 | — | — | |
| S23 | Machine #1 + RTP gate | pending | 0 | — | — | |
| S24 | Machines #2–3 | pending | 0 | — | — | config-only diff |
| S25 | Blackjack | pending | 0 | — | — | |
| S26 | European Roulette (wheel) | pending | 0 | — | — | |
| S27 | Baccarat | pending | 0 | — | — | |
| S28 | Video Poker (+ shared evaluator) | pending | 0 | — | — | CF: evaluator.py → S29 |
| S29 | 7-card hand evaluator | pending | 0 | — | — | extends S28 evaluator |
| S30 | Pot / side-pot engine | pending | 0 | — | — | |
| S31 | Poker table reducer (engine) | pending | 0 | — | — | |
| S32 | Realtime poker + redaction | pending | 0 | — | — | security review (no leak) |
| S33 | Matchmaking + buy-in + rake | pending | 0 | — | — | security review (ledger) |
| S34 | Timers + disconnect + reconnect | pending | 0 | — | — | realtime review (realtime-integrity-reviewer) |
| S35 | Anti-collusion signals | pending | 0 | — | — | security review |
| S36 | Faucets | pending | 0 | — | — | ledger review (ledger-security-reviewer) |
| S37 | Leaderboards + sinks | pending | 0 | — | — | ledger review (ledger-security-reviewer) |
| S38 | Responsible-gaming stubs | pending | 0 | — | — | |
| S39 | Observability | pending | 0 | — | — | |
| S40 | Load test (sign-off) | pending | 0 | — | — | ⛔ SIGN-OFF · deferred/live · needs S20 (+S35 only if poker built) |
| S41 | Deploy demo | pending | 0 | — | — | ⛔ DESTRUCTIVE/PAID · go required |
| S42 | Portfolio packaging | pending | 0 | — | — | |

> Status values: pending | in_progress | passed | blocked. Note carries review tier, CARRY-FORWARD (CF) constraints, and why a blocked step is blocked.

## Dependency graph
```
P1 Spine        S1 → S2 → S3 ;  S1 → S4 ;  S1 → S5 ;  {S2} → S8 ;  {S3,S4,S5} → S6 → S7
P2 Originals    {S6,S7} → S9(REVIEW GATE) → {S10,S11,S12,S13,S14,S15,S16,S17}   # parallel after S9
P3 Crash        S10 → S18 → S19 → S20
P4 Slots        {S6,S7} → S21 → S22 → S23 → S24
P5 Table        {S6,S7} → {S25, S26, S27, S28}
P6 Poker        S6 → S29 → S30 → S31 → S32(also needs S18) → {S33(+S3), S34} → S35
P7 Polish       S3→S36 ; S3→S37 ; S6→S38 ; S6→S39 ; S20→S40[SIGN-OFF] (+S35 if poker built) → S41[DEPLOY] → S42
```

## Log
> Append-only. One line per state change.
- 2026-06-27: Kit authored (plan + steps + orchestrator + this tracker). Status NOT STARTED, S1..S42 = pending. Next: paste the orchestrator prompt, run SETUP → S1.
