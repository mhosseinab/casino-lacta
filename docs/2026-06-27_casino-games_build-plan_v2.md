---
type: plan
project: casino-games
tags: [casino, originals, slots, table-games, poker, fastapi, python, play-money, portfolio, plan, dev]
status: draft (plan only — not building yet); gaps-patched 2026-06-27
version: v2 (merged)
stack: Python / FastAPI (async) · Postgres (SQLAlchemy 2.0 + Alembic) · Redis · WebSockets · pytest
scope: full games layer, social play-money, portfolio iGaming showcase
depends_on: 2026-06-27_casino-games_spec_v2.md
repo_target: casino-lacta/
supersedes_drafts: [2026-06-27_casino-games_build-plan.md, "2026-06-27_casino-games_build-plan copy.md"]
build_now: false
date: 2026-06-27
---

# Casino Games — Build Plan (v2, merged)

> **What this is.** The execution plan for the games layer specified in [[2026-06-27_casino-games_spec_v2]]. Merges the prior client-side React demo plan and the social-casino delivery plan into one **FastAPI / Python** build, framed as an iGaming **portfolio showcase** (social, play-money, non-redeemable).
>
> **File note.** This is a `_v2` file; the three prior drafts are left untouched per your vault rule.

---

## 0. Priority order (read first — I'm sequencing this)

You think in systems; I'll hold the order. Do **not** start with a game.

**The single most important first action: build the platform spine** — the double-entry **wallet/ledger** + the **RNG / provably-fair service** + an open **verifier** — before any game exists. Every game is a thin state machine on top of it, and these three are the parts that make this a *portfolio* piece rather than a toy. Build them once, correctly, with tests.

Priority order:
1. **Spine** (ledger + RNG/fairness + verifier + audit + RTP CI gate). ← start here
2. **One vertical slice** (Dice) to prove engine↔wallet↔verifier end-to-end.
3. **Ascending complexity**: instant Originals → Crash (first realtime) → slots framework → table games → **poker last** (heaviest).
4. **Portfolio packaging** continuously, not at the end — each phase must leave a demoable artifact.

Rationale for "spine first": it's the maximal-dependency, maximal-signal component. Getting the ledger and provable fairness right is the whole game; the individual games are comparatively mechanical once the spine holds.

> **Note on numbering.** The canonical *execution* numbering is **P1–P7 / S1–S42** (see plan.md + the implementation-steps doc + progress tracker). The **M0–M7** phases in this doc are a planning grouping that maps onto it — see the M↔P↔S cross-map in plan.md §6. Don't treat the M-numbers as execution order.

---

## 1. Scope reality-check (push-back before you commit)

**The full target is large for a solo portfolio piece.** Four game families + nine Originals + realtime Crash + realtime poker is a multi-month build. That's fine if the goal is a long-running flagship project — but for a *portfolio* the marginal blackjack clone adds little signal once the spine + a provably-fair game + a realtime game already exist.

**My recommendation — a defensible MVP cut that demonstrates 100% of the hard skills:**

| Tier | Build | Demonstrates |
|---|---|---|
| **MVP (ship this first)** | Spine + **Dice** + **Mines** + **Crash** | Provable fairness, double-entry ledger, stateful cash-out game, realtime multiplayer + cash-out integrity, RTP CI gate. *This alone is an impressive portfolio piece.* |
| **+1 (breadth)** | **Slots framework** + 1 machine, **Blackjack** | Data-driven math engine + RTP-as-CI, classic rules engine |
| **Stretch (high cost/risk)** | **Poker PvP**, remaining Originals/table games, Part D | Realtime multiplayer depth, anti-collusion — but **poker is the #1 scope risk** (§8) |

**Poker is flagged.** It is the highest-effort, highest-risk game (side pots, anti-collusion, realtime table state) and adds engineering surface a recruiter rarely needs to see *in addition to* Crash. Build it only if poker depth is a target role's requirement, or as a deliberate flagship. Everything below assumes the **full** target but is structured so you can stop after any phase with a coherent, shippable demo.

> If you'd rather I commit the plan to the MVP cut only, say so and I'll trim Phases 3–5.

---

## 2. Architecture

Server-authoritative FastAPI backend. The browser renders; it never decides outcomes or balances (spec §2.7) — which is why the prior browser-only React plan is superseded as the *product* (it survives only as an optional thin client).

```
            app/  (FastAPI: HTTP + WS, auth, DI)            engine/  (pure Python — no framework imports)
  ┌─────────────────────────────────┐         ┌──────────────────────────────────────────┐
  │ REST endpoints · WS gateways     │ intent  │ rng.py        HMAC-SHA256 seeded stream     │
  │ connection mgr (Redis pub/sub)   │ ──────▶ │ money.py      minor-units math + caps       │
  │ wallet/ledger service (txns)     │ outcome │ fairness.py   commit / reveal / verify       │
  │ Pydantic v2 schemas at boundary  │ ◀────── │ games,slots,table,poker  pure outcome logic │
  └─────────────────────────────────┘         └──────────────────────────────────────────┘
        │ SQLAlchemy async                         ▲ imported verbatim by verifier/ (parity)
        ▼                                          │
  Postgres (ledger, bets, rounds, seeds)     Redis (round/table state, rate limits, leaderboards, jackpot counters)
```

**The discipline (carried from the React plan, kept):** `engine/` is **pure Python** — no `fastapi`, no `sqlalchemy`, no I/O, no wall-clock in outcome logic. Only `app/` knows about the framework and DB. This keeps outcome logic unit-testable, deterministic, and **reused verbatim by the verifier** so server result == verifier result by construction.

**Repo structure** (repo root `casino-lacta/`; integration branch `casino-games/main`) — a **polyglot monorepo**: `engine`, `app`, and
`verifier` are uv workspace members (each a `src/`-layout package with its own `pyproject.toml`);
`client` + the generated contract are pnpm members. The split makes engine purity a *dependency
fact* — `engine`'s `pyproject.toml` lists no framework, so it cannot import one. Full configs,
tooling (uv + pnpm + Task + lefthook + import-linter), the contract seam, and the
production-readiness checklist live in `2026-06-27_casino-games_monorepo-blueprint.md`.
```
casino-lacta/                            # repo root (integration branch: casino-games/main)
  pyproject.toml          # uv workspace root (members: engine, app, verifier) + ruff + mypy(strict)
  pnpm-workspace.yaml  package.json   # TS workspace (client, packages/contracts-ts) + lefthook
  Taskfile.yml  lefthook.yml  .importlinter  biome.json
  docker-compose.yml  alembic.ini  .github/workflows/ci.yml
  engine/                          # uv member — deps = stdlib ONLY (purity by construction)
    pyproject.toml
    src/engine/                    # importable as `engine`
      rng.py  money.py  fairness.py  types.py  registry.py
      games/   dice.py limbo.py crash.py mines.py plinko.py hilo.py keno.py roulette.py pocketdice.py
      slots/   framework.py  machines/*.json
      table/   blackjack.py roulette_wheel.py baccarat.py video_poker.py
      poker/   evaluator.py table.py pots.py anticollusion.py
  app/                             # uv member — depends: engine{workspace}, fastapi, sqlalchemy…
    pyproject.toml  Dockerfile
    src/app/                       # importable as `app`
      main.py  deps.py
      api/         # REST routers per game
      ws/          # crash.py, poker.py (WebSocket gateways)
      wallet/      # double-entry ledger service + idempotency
      auth/        # JWT sessions (play-money; light)
  verifier/                        # uv member — depends: engine{workspace} ONLY
    pyproject.toml
    src/verifier/  +  web/         # reference code imports engine/ verbatim + the static page
  tests/         # *_rtp.py *_determinism.py *_rules.py *_sidepots.py  + load/   (root)
  client/        # pnpm member — Vite + React thin renderer (presentation only)
  packages/contracts-ts/   # GENERATED TS types from app's OpenAPI schema (the client's only contract)
  migrations/    # alembic  (root, owned by app)
```

---

## 3. Shared contracts (build once; every game conforms)

```python
# engine/src/engine/types.py  — pure, framework-free
from typing import Protocol, Any
from dataclasses import dataclass

class RngStream(Protocol):
    def next(self) -> float: ...                      # uniform [0,1), deterministic

@dataclass(frozen=True)
class GameConfig:
    edge: float
    params: dict[str, Any]

@dataclass(frozen=True)
class Outcome:
    multiplier: float                                  # payout computed by money.py
    detail: dict[str, Any]

class InstantGame(Protocol):                           # Dice, Limbo, PocketDice, Keno, Roulette, Plinko, slots, video poker
    id: str
    def play(self, input: dict, rng: RngStream, cfg: GameConfig) -> Outcome: ...

class StatefulGame(Protocol):                          # Mines, HiLo, Crash-bet, Blackjack, Baccarat
    id: str
    def init(self, input: dict, rng: RngStream, cfg: GameConfig) -> dict: ...   # commit layout from rng at start
    def step(self, state: dict, action: dict) -> tuple[dict, Outcome | None]: ...
```

**Shared bet loop** (in `app/`, reused by every game): validate + `debit` (ledger txn) → run `play` / `init`+`step` → `money.apply_multiplier` → `credit` (ledger txn) → write `AuditEvent` → return result; `GET /fairness/{betId}` exposes seeds/nonce/derivation + verifier link. A new game supplies only its pure outcome function + input/limit schema.

**RNG / fairness (engine).** `create_rng(server_seed, client_seed, nonce)` = HMAC-SHA256 stream → 32-bit floats. Commit `SHA256(server_seed)` shown before the bet; reveal on rotation; `verify(bet)` recomputes any past outcome. Identical scheme server-side and in `verifier/`.

---

## 4. Phased build

Each game ships as: **pure engine fn/reducer + wired endpoint + RTP test + determinism test + (Originals) verifier parity.** Effort is relative (solo, "sessions" not calendar) — estimates, not commitments.

| Phase | Theme | Deliverables | Exit criteria | Effort |
|---|---|---|---|---|
| **M0** | **Spine** | Repo, Docker(Postgres+Redis), CI(ruff+mypy+pytest), JWT auth, **double-entry ledger** (debit/credit/rollback/grant, idempotent), **RNG+fairness** module, **verifier**, limits/RG hooks, data model + migrations, audit, **RTP-gate harness** | Place a bet against a stub game; ledger balances & reconciles; fairness verifiable; CI green incl. a sample RTP test | ~2 sessions |
| **M1** | **Vertical slice** | **Dice** end-to-end (engine→wallet→API→verifier) | Dice RTP test passes (1e7 rolls ∈ 1−edge ±0.5%); verifier reproduces; **review gate before scaling** | ~1 |
| **M2** | **Instant Originals** | Limbo, Pocket Dice, Mines, Plinko, HiLo, Keno, Roulette(0–99) | Each: RTP/EV test + determinism + verifier parity; single-debit guarantee on stateful (Mines/HiLo) | ~½ each |
| **M3** | **Crash** | Realtime WS round actor, betting/run/crash loop, **server-authoritative cash-out**, auto-cashout, Redis fan-out, reconnect/resume, recovery | Crash-point distribution matches `P(C≥x)=(1−edge)/x` over 1e6; no double-credit; latency-fairness test; mid-round restart recovers | ~2–3 |
| **M4** | **Slots framework** | Data-driven engine (weighted strips, paytable, lines/ways, features), 2–3 machines, **per-machine RTP CI gate** | Each machine's RTP sim within tolerance in CI; feature-trigger rates correct | ~2–3 |
| **M5** | **Table & video poker** | Blackjack → European Roulette → Baccarat → Video Poker | Rules-engine property tests pass; edges match published values for configured rules; determinism | ~1 each |
| **M6** | **Poker PvP** ⚠️ | Realtime tables, matchmaking, hand evaluator, **side pots**, blinds/timers, disconnect, rake, anti-collusion signals | Full NLHE hand among N clients; side-pot math correct under multiple all-ins; no hole-card leakage; rake reconciles | ~4–6 (highest) |
| **M7** | **Economy + polish + packaging** | Faucets (daily/level/grant), leaderboards, RG stubs, README + architecture diagram + recorded demo, deployed instance, real-money-extension appendix | Deployed demo a reviewer can browse in 5 min; provable-fair verifier + RTP gate visibly demonstrated | ~2 |

**Dependency graph.**
```
M0 ──▶ M1 ──▶ M2 ──┐
        └────────▶ M3 (Crash; needs spine + WS + the `_curve.py` helper authored in the Limbo step / S10) ──▶ M6 (Poker reuses realtime foundation)
M0 ──────────────▶ M4 (Slots) 
M0 ──────────────▶ M5 (Table) 
all ─────────────▶ M7 (packaging)
```
M3 (Crash) needs the spine + WS plus the `_curve.py` helper first created in the Limbo step (S10) — a hard carry-forward edge — but not the rest of the instant games. M3 (Crash) is in turn the realtime prerequisite for M6 (Poker). M4/M5 are independent of the realtime track and can interleave.

---

## 5. Definition of done — per game
- [ ] Pure engine fn/reducer in `engine/…` — no `app`/DB/framework imports, no wall-clock in outcome logic.
- [ ] **RTP/EV test**: empirical RTP over ≥1e6 trials ∈ `target ± tolerance` (ports spec Appendix A). For slots, a per-machine sim gate.
- [ ] **Determinism test**: same `(serverSeed, clientSeed, nonce, input)` → identical outcome.
- [ ] Wired to the shared bet loop (single-debit/single-credit via the ledger; idempotent).
- [ ] **Verifier parity** (Originals): `verify()` reproduces the result from the seed.
- [ ] Max-win cap + per-currency bet-limit enforced; RG pre-bet gate honored.
- [ ] `AuditEvent` written linking outcome → seed/nonce/input/config-version.

---

## 6. Testing = verification (the headline)
- **RTP as a CI gate** is the signature artifact: CI fails if any game's measured RTP drifts from target. It proves the math, automatically, on every commit. Lead with this in the portfolio.
- **Property tests** (Hypothesis) for rules engines: blackjack payout/bust/split, poker hand-rank invariants, side-pot conservation (chips in == chips out).
- **Determinism + verifier-parity** across all Originals (a `verify-parity` test over a batch of random bets).
- **Concurrency**: parallel bets can't double-spend; rapid Mines reveals can't double-advance; duplicate Crash cash-out can't double-credit.
- **Load**: one Crash round with N concurrent clients; one poker table under timer churn.

---

## 7. Tooling & deploy
- **Deps:** `fastapi`, `uvicorn`, `pydantic`, `sqlalchemy[asyncio]`, `alembic`, `redis`, `pytest`, `hypothesis`, `ruff`, `mypy`. Client (optional): Vite + React + canvas/PixiJS.
- **Local:** `docker-compose up` (API + Postgres + Redis). **CI:** GitHub Actions — ruff + mypy + pytest + RTP gate.
- **Deploy demo:** container on Fly.io / Render / Railway + managed Postgres + Redis; thin client static → GitHub Pages / Cloudflare Pages. A live URL matters for a portfolio.

---

## 8. Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **Poker scope creep** (side pots, anti-collusion, realtime state) | **High** | Treat as stretch (§1); build only after Crash proves the realtime foundation; timebox; cut tournaments from v1 |
| **Scope explosion across 4 families** | High | Ship the MVP cut (§1) first; every phase leaves a demoable artifact; stop anywhere |
| **RNG / math correctness** | High | Engine is pure + reused by verifier; RTP CI gate; property tests; math already re-verified (spec Appendix A) |
| **Realtime concurrency bugs** (double-credit, latency arbitrage) | High | Single authoritative round/table actor; idempotent ledger ops; server-timestamped cash-out; recovery from committed seed |
| **Ledger correctness** | High | Double-entry append-only; reconciliation job; idempotency keys; load test before each phase gate |
| **FastAPI realtime throughput** for Crash ticks | Medium | Throttle advisory ticks; outcome fixed at round start (ticks are cosmetic); Go sidecar is the documented escape hatch (spec §2.11) |
| **Over-building vs portfolio ROI** | Medium | The MVP cut already demonstrates every hard skill; resist gold-plating |

---

## 9. Portfolio packaging (what a reviewer should see)
Map each artifact to an iGaming competency, and surface them in the README:
- **Provably-fair verifier** (open page, recompute any bet) → cryptographic fairness, the differentiated skill.
- **RTP CI gate** (green check proving every game's math) → game-math rigor + test engineering.
- **Double-entry ledger that reconciles to the cent** → financial-systems correctness.
- **Server-authoritative Crash with cash-out integrity** → realtime + anti-cheat.
- **(If built) poker side-pot engine + anti-collusion** → hard realtime multiplayer.
- **Real-money extension appendix** (spec §5) → you understand the regulated context, not just toys.

README order: 60-second demo GIF → "how fairness works" + verifier link → architecture diagram → RTP-gate screenshot → run-locally. Optimize for a 5-minute skim.

---

## 10. Trade-offs already decided

| Decision | Choice | Why / cost |
|---|---|---|
| Backend | **FastAPI / Python** | Your primary stack; one language for engine + verifier. Cost: a Go sidecar may be needed for very high-rate Crash (documented, not built) |
| Outcome logic | **Pure `engine/` package** | Deterministic, testable, verifier-reusable. Cost: discipline — no framework/DB leaks into `engine/` |
| Money model | **Social play-money, non-redeemable** | Small legal surface; real-money is an unwired seam |
| Client | **Thin renderer, optional** | Backend is the showcase; presentation is secondary and never authoritative |
| Crash/Plinko outcome | **Computed in engine; animation cosmetic** | Real physics is non-deterministic/unverifiable |

---

## 11. Deferred to build time
1. Exact Plinko/Keno/slot published multiplier tables (method in spec; tune numbers to target RTP after rounding).
2. Auth depth (guest → linked account) — minimal for a play-money demo.
3. Client engine choice (React+canvas vs PixiJS) — validate with a slot vertical-slice spike if slots ship.
4. Provably-fair extension to slots/table/poker (recommended; optional for v1).
5. Visual theme / branding — separate task.

---

## 12. Next action
Plan is saved; **not building yet** per your call. On "go", I start at **M0 (platform spine)** and stop after **M1 (Dice end-to-end)** for your review. Repo root: `casino-lacta/` (already exists, git-init'd on `master`; integration branch `casino-games/main`).

**Decision I need from you when you're ready:** full target, or the MVP cut (§1)? Default recommendation: **MVP cut first**, decide on poker after Crash is working.

---
*Plan v2 · 2026-06-27 · merges the client-side React demo plan + social-casino delivery plan · stack FastAPI/Python · depends on [[2026-06-27_casino-games_spec_v2]] · social play-money, non-redeemable.*

## Related vault notes
[[2026-06-27_casino-games_spec_v2]] · [[provably-fair]] · [[double-entry-ledger]] · [[fastapi]] · [[crash-game-architecture]] · [[poker-hand-evaluator]] · [[server-authoritative]]
