---
type: plan
project: casino-games
tags: [casino, igaming, migration-orchestration, fastapi, plan, dev]
status: READY TO EXECUTE; gaps-patched 2026-06-27
slug: casino-games
date: 2026-06-27
depends_on: [2026-06-27_casino-games_spec_v2.md, 2026-06-27_casino-games_build-plan_v2.md]
companions: [2026-06-27_casino-games_implementation-steps.md, 2026-06-27_casino-games_orchestrator-prompt.md, 2026-06-27_casino-games_progress.md]
---

# Casino Games — Orchestration Plan (the *why*)

**Status: READY TO EXECUTE (2026-06-27).** Build the full games layer of a social, play-money casino as a Python/FastAPI, server-authoritative backend — provably-fair Originals, a data-driven slots framework, classic table/video-poker games, and realtime PvP poker — sequenced as ~50 standalone, independently verifiable steps. **The single most important boundary: this is a greenfield build of the GAMES layer only; it is play-money and non-redeemable, and real-money is designed-for but never enabled.**

> Supersedes: the three prior drafts (`..._spec.md`, `..._build-plan.md`, `..._build-plan copy.md`). Specs to read for mechanics: [[2026-06-27_casino-games_spec_v2]] (math, contracts, per-game specs) and [[2026-06-27_casino-games_build-plan_v2]] (phases, repo layout, DoD). The *how* (steps) lives in the companion implementation-steps doc.

---

## 1. Why, and the honest scope

**End state:** a runnable FastAPI service + pure-Python `engine/` package implementing every game in spec_v2, with a double-entry coin ledger, a provably-fair RNG service + open verifier, an RTP-as-CI-gate, and a deployed play-money demo — packaged as an iGaming portfolio piece.

**What does NOT change / is NOT in scope (say it plainly):**
- **No real money.** No payment rails, no KYC/AML, no cash-out, no prizes, no sweepstakes. `mode=REAL` exists in the model but is never wired (spec_v2 §5). This is the load-bearing boundary — every "compliance" item is *awareness/seam*, not built.
- **No third-party game integration** (spec_v2 Part D) in this kit — optional, deferred.
- **Client is a thin renderer, not the deliverable.** The backend is the showcase; the web client is presentation only and never authoritative.
- **Not a clone of any specific operator.** Originals are modeled on standard crypto-casino designs; exact published payout tables are tuned to target RTP, not copied.

**Why it's lower-risk than it sounds:** the math is already designed and **re-verified (43/43, spec_v2 Appendix A)**; the architecture and contracts are fully specified. The genuinely new work is *implementation discipline* (engine purity, idempotent ledger, realtime integrity) — not research. The real risk concentrates in two places: **realtime integrity** (Crash cash-out, poker state) and **poker scope** (side pots, anti-collusion).

## 2. Current state (verified against the workspace 2026-06-27)

> Greenfield. Grounded by `ls` this session, not memory.

| Piece | Location | Status today |
|---|---|---|
| Spec (merged) | `OUTPUTS/dev/2026-06-27_casino-games_spec_v2.md` | Complete; math re-verified |
| Build plan (merged) | `OUTPUTS/dev/2026-06-27_casino-games_build-plan_v2.md` | Complete; phases M0–M7, DoD, repo layout |
| Prior drafts (×3) | `OUTPUTS/dev/2026-06-27_casino-games_{spec,build-plan,build-plan copy}.md` | Superseded; left untouched |
| Repo | `casino-lacta/` (workspace root) | **EXISTS** — git-init'd (branch `master`, 0 commits); only `docs/` + `.claude/` + `CLAUDE.md` present. Code still greenfield, created by S1 on `casino-games/main` |
| Rules doc | `~/Documents/Claude/CLAUDE.md` | Exists — Python primary, English only, output conventions, model/delegation |

**Invariants the build must preserve** (from CLAUDE.md + spec_v2): Python primary; English only; outputs date-prefixed; **engine purity**, **minor-units money**, **server-authoritative outcomes**, **double-entry ledger**, **idempotency**, **determinism**, **provable fairness for Originals** — all carried into §4 and the steps doc's "project rules".

## 3. Target architecture

```
BEFORE                                  AFTER  (polyglot monorepo: uv + pnpm + Task — see monorepo-blueprint)
──────                                  ─────
3 conflicting design drafts             casino-lacta/  (git, integration branch casino-games/main)
no code, no repo                        engine/   uv member — PURE Python (stdlib-only deps): src/engine/
                                                  {rng,fairness,money,types,registry}.py games/ slots/ table/ poker/
                                        app/      uv member — FastAPI (HTTP + WS), wallet/ledger, auth: src/app/
                                        verifier/ uv member — reuses engine/ → provable-fair parity: src/verifier/
                                        client/   pnpm member — thin React renderer (+ packages/contracts-ts)
                                        tests/    RTP gates, determinism, rules, side-pots, load
                                        Postgres (ledger/bets/seeds) + Redis (round/table state)
```

What the shape buys: outcome logic is deterministic, unit-testable, and reused verbatim by the verifier (server == verifier by construction). The monorepo split makes the discipline structural — `engine/` is its own package whose dependencies are stdlib-only, so it *cannot* import `fastapi`/`sqlalchemy`/IO/clock (an `import-linter` contract + the purity grep back it up).

## 4. Key design decisions (the real forks — all decided before S1)

### 4.1 Money model — social play-money, non-redeemable
**Decision (2026-06-27):** virtual `GOLD` only; no cash-out/prizes/sweepstakes. Why: smallest legal surface; matches the brief. Trade: no real-money revenue path in v1. Consequences: `mode=REAL` modeled but unwired; RTP is an economy pacing knob, not a payout obligation.

### 4.2 Backend stack — Python / FastAPI (async)
**Decision:** FastAPI + Pydantic v2 + SQLAlchemy 2.0 async + Alembic + Redis; Starlette WebSockets for realtime. Why: owner's primary stack; one language for engine + verifier. Trade: for very-high-rate Crash ticking a Go sidecar is the production move — **documented as a seam, not built** (spec_v2 §2.11).

### 4.3 Engine purity — `engine/` is framework-free
**Decision:** no `fastapi`/`sqlalchemy`/IO/wall-clock in outcome logic; only `app/` touches framework/DB. Why: testability, determinism, verifier reuse. Consequence: a lint/review check guards engine imports.

### 4.4 Fairness model — provably fair for Originals; server-authoritative + audit elsewhere
**Decision:** commit–reveal (server/client seed + nonce) for all Originals + an open verifier; slots/table/video-poker = server-authoritative CSPRNG + per-round audit (provably-fair shoe/reel commit recommended, optional); poker = CSPRNG shuffle + per-seat redaction + anti-collusion. Why: provable fairness is the differentiated portfolio signal, cheapest where it matters most.

### 4.5 Scope — full target
**Decision:** all Originals + slots framework + table/video poker + PvP poker. Trade: large surface, multi-month. Mitigation: phased so any phase boundary is a coherent stop; **poker (P6) is the highest-risk phase** and may be cut without affecting earlier value.

### 4.6 Correctness gate — RTP-as-CI
**Decision:** every game ships an RTP/EV test; CI fails on drift beyond tolerance. Why: proves the math automatically; headline portfolio artifact. Consequence: a shared RTP-harness is built in the spine (S7).

### 4.7 Execution model — branch-per-step on an integration branch, orchestrator driver
**Decision:** `casino-games/main` integration branch; each step on `casino-games/S<N>`; in-context subagent orchestrator (not a dynamic workflow) because of inline human review gates. Commit convention: **Conventional Commits**. Why: clean scoped diffs, cheap rollback, inline gates.

## 5. Protocol / interface changes (the contracts every step honors)

- **Wallet/Bet contract** (spec_v2 §2.2): `debit`/`credit`/`rollback`/`grant`, all idempotent on `idempotencyKey`; single-debit/single-credit per bet; settle is atomic. Built in S3.
- **Fairness API** (spec_v2 §2.3): `serverSeedHash` commit before bet; reveal on rotation; `GET /fairness/{betId}`; verifier recomputes. Built in S4/S7.
- **Game API envelope** (spec_v2 §2.10): `POST /games/{id}/bet`, `/action`, `GET /state`. Per-game in their steps.
- **Realtime protocols**: Crash WS (S18–20) and Poker WS (S32) message schemas per spec_v2 §A.4 / Part C.
- **Data model** (spec_v2 §2.9): tables built in S2; later migrations are additive.

## 6. Phased plan (P1 … P7)

Ordering invariant: **build + verify the spine before any game; introduce realtime incrementally (Crash before Poker); destructive/paid (deploy) and human-sign-off (load test) last.** Each phase ends demoable.

> **Phase numbering cross-map.** Canonical execution numbering is **P1–P7 / S1–S42** (this doc + the steps doc + progress tracker). build-plan_v2 uses **M0–M7** and spec_v2 §6 uses **"Phase 0–6"**; they map as below — don't read M-numbers or spec Phase-numbers as execution order.
>
> | Canonical (exec) | build-plan M | spec_v2 Phase | Steps |
> |---|---|---|---|
> | P1 Spine | M0 | Phase 0 | S1–S8 |
> | P2 Instant Originals | M1 + M2 | Phase 1 | S9–S17 |
> | P3 Crash | M3 | Phase 2 | S18–S20 |
> | P4 Slots | M4 | Phase 3 | S21–S24 |
> | P5 Table & video poker | M5 | Phase 4 | S25–S28 |
> | P6 Poker PvP | M6 | Phase 5 | S29–S35 |
> | P7 Economy / polish / packaging | M7 | Phase 6 | S36–S42 |

- **P1 — Spine (S1–S8).** Repo, ledger, RNG/fairness, verifier, RTP harness, contracts, auth. **Acceptance:** a bet against a stub game debits/credits via the ledger, reconciles, is fairness-verifiable; CI green incl. a sample RTP gate.
- **P2 — Instant Originals (S9–S17).** Dice (slice + **review gate**) → Limbo, Pocket Dice, Mines, Plinko, HiLo, Keno, Roulette 0-99, optional extensions. **Acceptance:** each passes RTP/EV + determinism + verifier parity.
- **P3 — Crash (S18–S20).** Realtime round actor, server-authoritative cash-out, recovery. **Acceptance:** crash-point distribution matches `P(C≥x)=(1−edge)/x` over 1e6; no double-credit; latency-fairness holds.
- **P4 — Slots framework (S21–S24).** Data-driven engine + features + machines, per-machine RTP gate. **Acceptance:** each machine's RTP sim within tolerance in CI.
- **P5 — Table & video poker (S25–S28).** Blackjack, European Roulette, Baccarat, Video Poker. **Acceptance:** rules engines property-tested; edges match published values for configured rules.
- **P6 — Poker PvP (S29–S35) ⚠️ highest risk.** Evaluator, side pots, table actor, realtime, matchmaking, timers/disconnect, anti-collusion. **Acceptance:** full NLHE hand among N clients; side-pot conservation; no hole-card leakage; rake reconciles.
- **P7 — Economy, polish, packaging (S36–S42).** Faucets, leaderboards, RG stubs, observability, **load test (sign-off)**, **deploy (paid/gated)**, portfolio packaging. **Acceptance:** deployed demo a reviewer browses in 5 min; verifier + RTP gate visibly demonstrated.

## 7. Decommission / cleanup checklist

Greenfield — minimal. The only cleanup is documentation hygiene:
```
OUTPUTS/dev/2026-06-27_casino-games_spec.md ............ keep (superseded; frontmatter note only)
OUTPUTS/dev/2026-06-27_casino-games_build-plan.md ...... keep (superseded)
OUTPUTS/dev/2026-06-27_casino-games_build-plan copy.md . keep (superseded) — or delete on your call
the 4 kit docs already live in casino-lacta/docs/ ..... in-repo source of truth (repo already exists)
```

## 8. Rule & doc updates
- Add a `casino-lacta/` entry to the Projects MOC / `_MOCs/Dev.md` (the repo already exists; first code lands at S1).
- The `casino-lacta/docs/` copies are the in-repo source of truth. Land any spec change in the same commit as the behavior change.

## 9. Privacy / security / compatibility impact
- **Trust boundary:** the client is hostile; the server decides every outcome and balance. Enforced by engine purity + server-authoritative checks + per-seat redaction (poker).
- **PII:** minimal — auth identifiers only (play-money guest/JWT). No payment data, no KYC. GDPR surface is small but honor data-deletion for accounts.
- **No real-money** → no licensing/AML obligations in v1 (spec_v2 §5). Do not represent the build as licensable.

## 10. Risks & rollback

| Risk | Mitigation / rollback |
|---|---|
| Poker scope creep (P6) | Highest-risk phase, sequenced last; cut entirely without affecting P1–P5 value; timebox; no tournaments in v1 |
| Realtime integrity bugs (double-credit, latency arb) | Single authoritative round/table actor; idempotent ledger; server-timestamped cash-out; recovery from committed seed; dedicated tests (S20, S30, S32) |
| Ledger correctness | Double-entry append-only; reconciliation job (S3); idempotency; load test (S40) before deploy |
| RTP/math drift | RTP-as-CI gate (S7); property tests; math pre-verified (Appendix A) |
| FastAPI realtime throughput | Throttle advisory ticks; outcome fixed at round start; Go sidecar seam documented |
| Scope explosion across families | Phase boundaries are coherent stops; rollback = leave later phases unbuilt |

## 11. Acceptance criteria (whole change)
- Spine: ledger reconciles to the cent under concurrent bets; fairness verifiable; RTP harness wired to CI.
- Every game: RTP/EV test within tolerance + determinism + (Originals) verifier parity, all green in CI.
- Crash + Poker: realtime integrity tests pass (no double-credit, no hole-card leak, side-pot conservation).
- A deployed play-money demo with a working provably-fair verifier and a visible RTP CI gate.
- `engine/` contains zero framework/DB imports (enforced by a check).

## 12. Implementation map
- **Spine:** `app/src/app/wallet/`, `engine/src/engine/{rng,fairness,money,types,registry}.py`, `app/src/app/api`, `app/src/app/auth`, `verifier/src/verifier/`, `tests/` harness, `migrations/`.
- **Originals:** `engine/src/engine/games/*.py` + `app/src/app/api/originals/*`.
- **Crash/Poker realtime:** `app/src/app/ws/{crash,poker}.py`, `engine/src/engine/poker/*`, Redis state.
- **Slots/Table:** `engine/src/engine/slots/*`, `engine/src/engine/table/*`.
- **Economy/packaging:** `app/src/app/economy/*`, `client/`, `README.md`, `docs/`.

Sources (verified in-workspace 2026-06-27): `casino-lacta/docs/2026-06-27_casino-games_spec_v2.md`, `..._build-plan_v2.md`, `casino-lacta/CLAUDE.md`; `ls casino-lacta/` (repo root exists, git-init'd on `master`; `app/`/`engine/`/`verifier/` not yet created — greenfield until S1).

## Related vault notes
[[2026-06-27_casino-games_spec_v2]] · [[2026-06-27_casino-games_build-plan_v2]] · [[provably-fair]] · [[double-entry-ledger]] · [[fastapi]] · [[migration-orchestration]]
