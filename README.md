# Casino (codename **lacta**)

> A social, **play-money** casino — the **games layer**, built **server-authoritative**.
> Pure-Python outcome logic behind a FastAPI service with a double-entry coin ledger, an
> open provably-fair verifier, and **RTP-as-a-CI-gate**.

`GOLD` is play-money and **non-redeemable**. `mode=REAL` is *modeled* in the schema but
**never wired** — no payment rails, no KYC/AML, no cash-out, no prizes. Every "compliance"
item is an awareness seam, not a built feature. This is not represented as licensable.

**Stack:** Python 3.12 · FastAPI (async) · Pydantic v2 · SQLAlchemy 2.0 + Alembic · Postgres ·
Redis · Starlette WebSockets. Polyglot monorepo: **uv** (Python) + **pnpm** (TS client) wired
by **Task**, with **lefthook** git hooks, **import-linter** seam contracts, **ruff/mypy/biome**.

---

## Table of contents

1. [The one load-bearing idea](#1-the-one-load-bearing-idea)
2. [Architecture — staged granularity](#2-architecture--staged-granularity)
   - [L0 · System context](#l0--system-context)
   - [L1 · Packages & the dependency seam](#l1--packages--the-dependency-seam)
   - [L2 · The bet loop (request lifecycle)](#l2--the-bet-loop-request-lifecycle)
   - [L3 · Game seam, RNG & provable fairness](#l3--game-seam-rng--provable-fairness)
   - [L4 · Double-entry ledger](#l4--double-entry-ledger)
3. [Repository map](#3-repository-map)
4. [Games implemented](#4-games-implemented)
5. [Build — step by step](#5-build--step-by-step)
6. [Run locally](#6-run-locally)
7. [Test, lint & the RTP gate](#7-test-lint--the-rtp-gate)
8. [API surface](#8-api-surface)
9. [Deployment](#9-deployment)
10. [CI](#10-ci)
11. [Project status](#11-project-status)

---

## 1. The one load-bearing idea

**The server decides every outcome and balance; the client only renders.** Two structural
facts make that trustworthy and testable:

- **Engine purity.** `engine/` is a stdlib-only package — no `fastapi`, `sqlalchemy`, `redis`,
  network, file, wall-clock, and **never `random`/`secrets`**. Entropy enters outcome logic
  *only* through an injected `RngStream`. Purity is a **dependency fact** (`engine` declares no
  framework deps) and is enforced by `import-linter` + a grep gate.
- **One outcome implementation.** Because the engine is pure and deterministic, the open
  **verifier imports it verbatim** — the server result *equals* the verifier result by
  construction, never a re-implementation that can drift.

Everything else (money as integer minor units, the double-entry ledger, idempotency, RTP-as-CI)
hangs off those two.

---

## 2. Architecture — staged granularity

Read top-down: each diagram zooms one level into the box above it.

### L0 · System context

```mermaid
flowchart LR
  client["Client<br/>(thin renderer — hostile,<br/>decides nothing)"]
  verifier["Open Verifier<br/>(static page + engine)"]
  api["FastAPI service<br/>(server-authoritative)"]
  pg[("Postgres<br/>ledger · configs · state")]
  redis[("Redis<br/>realtime pub/sub")]

  client -- "REST: intent only" --> api
  client -- "WebSocket: Crash" --> api
  client -- "verify a bet" --> verifier
  api -- "balances · outcomes · seeds" --> pg
  api -- "round/table fan-out" --> redis
  verifier -. "imports engine verbatim<br/>(server == verifier)" .-> api
```

### L1 · Packages & the dependency seam

The arrows only point **one way**: `app` and `verifier` may depend on `engine`; `engine`
depends on nothing but the standard library. `import-linter` fails the build on any violation.

```mermaid
flowchart TD
  subgraph py["uv workspace (Python)"]
    app["app/<br/>FastAPI · DB · IO · transport<br/>auth · wallet · bet_loop · ws · economy · rg"]
    verifier["verifier/<br/>proves outcomes<br/>(imports engine ONLY)"]
    engine["engine/  ★ PURE, stdlib-only<br/>rng · fairness · money · types · registry<br/>games/ · slots/ · table/ · cards/"]
  end
  subgraph ts["pnpm workspace (TypeScript)"]
    contracts["packages/contracts-ts<br/>(generated from OpenAPI)"]
    web["client/<br/>thin renderer (Vite/React/Pixi)"]
  end

  app -->|depends on| engine
  verifier -->|depends on| engine
  web -->|consumes generated types| contracts
  contracts -.->|generated from| app
  engine -. "NO framework / IO / clock / random" .- engine

  classDef pure fill:#13361f,stroke:#3fb950,color:#e6ffe6;
  class engine pure;
```

**Seams (swap = config/adapter change, not a rewrite):** RNG source (`RngStream` protocol) ·
Game (`InstantGame`/`StatefulGame` + `registry`) · Verifier==server (one engine, reused) ·
Persistence (Postgres/Redis behind interfaces; engine knows neither) · Money/economy policy
(`engine/money.py` is the single owner of rounding/caps; RTP targets & limits are config) ·
Transport (REST + WS gateways) · Client↔server contract (OpenAPI → generated TS).

### L2 · The bet loop (request lifecycle)

The single shared place-bet flow. **Validate → debit → resolve → credit**, each step idempotent,
money moving only via balanced ledger pairs.

```mermaid
sequenceDiagram
  autonumber
  participant C as Client
  participant API as app/api/games.py
  participant BL as games/bet_loop.py
  participant G as engine game (pure)
  participant RNG as engine/rng.py
  participant L as wallet/ledger.py
  participant DB as Postgres

  C->>API: POST /games/{id}/bet (intent + clientSeed)
  API->>BL: place_bet(game_id, input)
  BL->>G: validate_input(input, cfg)  %% pure fence, pre-debit
  BL->>L: DEBIT stake (idempotencyKey = hash(betId,"debit"))
  L->>DB: append balanced 2-row LedgerEntry (player + house)
  BL->>RNG: create stream (serverSeed, clientSeed, nonce)
  BL->>G: play(input, rng, cfg) -> Outcome(multiplier, detail)
  BL->>BL: money.apply_multiplier (FLOOR) then cap(maxWin)
  BL->>L: CREDIT payout (idempotencyKey = hash(betId,"credit"))
  L->>DB: append balanced 2-row LedgerEntry
  API-->>C: BetObject (outcome + new balance projection)
```

Stateful games (Mines, HiLo, Blackjack, Video Poker, Crash-bet) open a round via the same debit,
then advance through `POST /games/{id}/action`; settlement credits on the terminal step.

### L3 · Game seam, RNG & provable fairness

A new game is a **new conformance + a registry entry** — the bet loop, ledger, and API never
change (OCP). Entropy is a deterministic HMAC-SHA256 stream, so an outcome is a pure function of
`(serverSeed, clientSeed, nonce, input)`.

```mermaid
flowchart TD
  reg["registry.py<br/>id → (module, GameConfig)"]
  proto["Protocols (types.py)"]
  inst["InstantGame<br/>validate_input · play"]
  stat["StatefulGame<br/>validate_input · init<br/>public_view (redaction) · step"]
  rng["RngStream protocol<br/>next() ∈ [0,1)"]
  hmac["HmacRngStream<br/>resumable from (seed,seed,nonce,cursor)"]

  reg --> proto
  proto --> inst
  proto --> stat
  inst --> rng
  stat --> rng
  rng --> hmac

  subgraph fair["Provable fairness (commit–reveal)"]
    commit["1 commit: publish serverSeedHash BEFORE bet"]
    play2["2 play: outcome from sealed serverSeed"]
    reveal["3 reveal: serverSeed on rotation / terminal"]
    vfy["GET /fairness/{betId}: seeds + nonce + derivation + verifier link"]
    commit --> play2 --> reveal --> vfy
  end
```

Seed **generation** (CSPRNG via `secrets`) lives in `app/` only — never in `engine/`.
`public_view` is the single redaction point: while a round is ACTIVE it withholds hidden state
(unrevealed Mines cells, the dealer hole card *and* the undrawn shoe); a terminal status may
disclose it for verification.

### L4 · Double-entry ledger

Coins move **only** by writing an append-only, balanced two-row `LedgerEntry` set sharing one
`txnId`, whose counterparty is the reserved `SYSTEM`/house account. `wallet.balance` is a
**reconciled projection** — never mutated directly.

```mermaid
flowchart LR
  bet["place_bet / settle"] --> txn["one txnId"]
  txn --> e1["LedgerEntry: player −stake"]
  txn --> e2["LedgerEntry: house +stake"]
  e1 --> proj["wallet.balance<br/>(projection)"]
  e2 --> sys["house wallet<br/>(projection)"]

  inv1["reconcile(walletId):<br/>balance == Σ entries (to the cent)"]
  inv2["system-wide:<br/>Σ(all entries) == 0"]
  proj -.-> inv1
  sys -.-> inv2
```

**Invariants (tested under concurrency):** per-wallet `balance == sum(entries)`, system-wide
`Σ = 0`, single-debit/single-credit per bet, idempotent re-sends return the original result.

---

## 3. Repository map

```
casino-lacta/
├── engine/                     ★ uv member — PURE, stdlib-only outcome logic
│   └── src/engine/
│       ├── rng.py              HMAC-SHA256 seeded stream
│       ├── fairness.py         commit / reveal / verify
│       ├── money.py            integer minor-units math (floor + cap; single owner)
│       ├── types.py            InstantGame / StatefulGame / RngStream protocols
│       ├── registry.py         id → (module, GameConfig)  (the OCP seam)
│       ├── sampling.py         sample_without_replacement
│       ├── games/              Originals (dice, limbo, pocketdice, roulette99,
│       │                         plinko, keno, mines, hilo) + _curve.py + stub_coinflip
│       ├── slots/              framework.py (config-driven) + features.py
│       ├── table/              roulette_wheel · baccarat · video_poker · blackjack
│       └── cards/evaluator.py  5-card ranking (carry-forward → best-5-of-7 for poker)
├── app/                        uv member — all framework / DB / IO (depends on engine)
│   ├── Dockerfile              multi-stage uv build, non-root runtime
│   └── src/app/
│       ├── main.py             FastAPI app + router wiring + /health
│       ├── api/                games.py · fairness.py · crash.py (REST surface)
│       ├── auth/               guest/JWT sessions (router · service · tokens · deps)
│       ├── db/                 models.py (SQLAlchemy) · session.py
│       ├── wallet/ledger.py    double-entry ledger
│       ├── games/bet_loop.py   the shared place-bet flow
│       ├── ws/                 crash.py · crash_core.py · crash_bets.py (realtime actor)
│       ├── economy/faucets.py  play-money grants
│       └── rg/gate.py          responsible-gaming gate
├── verifier/                   uv member — imports engine verbatim + static web/index.html
├── tests/                      RTP/EV gates · determinism · verifier-parity · rules ·
│                                 ledger concurrency · rtp_harness.py (shared run_rtp)
├── migrations/                 Alembic (every migration reversible)
├── client/                     thin web renderer (Vite/React/Pixi) — presentation only
├── packages/contracts-ts/      generated TS types (OpenAPI seam)
├── docs/                       spec_v2 · build-plan_v2 · monorepo-blueprint · progress
├── Taskfile.yml  docker-compose.yml  .importlinter  pyproject.toml  uv.lock
└── .github/workflows/ci.yml
```

---

## 4. Games implemented

| Family | Game id | Kind | Notes |
|---|---|---|---|
| Originals | `originals.dice` | Instant | provably-fair vertical slice |
| Originals | `originals.limbo` | Instant | uses `_curve.py` |
| Originals | `originals.pocketdice` | Instant | |
| Originals | `originals.roulette` | Instant | 0–99 colour-pick |
| Originals | `originals.plinko` | Instant | per-(rows,risk) tables in config |
| Originals | `originals.keno` | Instant | per-(picks,risk) payout table |
| Originals | `originals.mines` | Stateful | server-held layout, redacted view |
| Originals | `originals.hilo` | Stateful | per-step entropy |
| Realtime | `originals.crash` | Realtime | WS actor; server-stamped cash-out |
| Table | `table.roulette` | Instant | 0–36 wheel, payout table |
| Table | `table.baccarat` | Instant | fixed third-card tableau; edges enumerated |
| Table | `table.video_poker` | Stateful | 9/6 JoB; ranks via `cards.evaluator` |
| Table | `table.blackjack` | Stateful | configurable rules; shoe + hole-card redaction |
| (test stub) | `stub.coinflip` | Instant | scaffold/parity fixture |

Slots (`slots/framework.py` + `features.py`) are a config-driven framework; the first machine
config + its RTP gate land in the slots phase.

---

## 5. Build — step by step

### Prerequisites

| Tool | Why | Check |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python workspace + venv | `uv --version` |
| [pnpm](https://pnpm.io/) + Node 20+ | TS client + lefthook | `pnpm -v` · `node -v` |
| [Task](https://taskfile.dev/) | cross-language entry points | `task --version` |
| Docker (+ Compose) | Postgres + Redis | `docker --version` |

Verify your toolchain at a glance:

```bash
task doctor      # prints OK/MISS for uv, pnpm, node, docker, task, lefthook
```

### Bootstrap

```bash
# 1. Clone and enter
git clone <repo-url> casino-lacta && cd casino-lacta

# 2. Install everything + git hooks (uv sync + pnpm install + lefthook install)
task setup

# 3. Bring up scratch infra (Postgres + Redis)
docker compose up -d postgres redis

# 4. Create the schema AND seed the SYSTEM user + house-GOLD wallet.
#    These exist ONLY via the data migration — a bare DB is not enough.
DATABASE_URL=postgresql+asyncpg://lacta:lacta@localhost:5432/lacta \
  uv run alembic upgrade head
```

> **Why the migration matters:** the double-entry ledger's counterparty is a reserved
> `SYSTEM`/house wallet inserted by `seed_configs()` in the initial migration. The DB-backed
> tests run against a real **alembic-migrated** Postgres (not `create_all`), so skipping the
> migrate step makes every ledger test error.

---

## 6. Run locally

```bash
# API with reload (also starts postgres + redis via compose)
task dev
# → uvicorn app.main:app --reload  on  http://localhost:8000
```

Smoke-check it:

```bash
curl -s http://localhost:8000/health
# OpenAPI / Swagger UI:
open http://localhost:8000/docs
```

Run the whole stack (API + DB + Redis) in containers instead:

```bash
docker compose up --build           # builds app/Dockerfile, exposes :8000
# then, once: migrate inside the running api container
docker compose exec api alembic upgrade head
```

Environment variables (defaults shown — compose sets them for you):

| Var | Default | Used by |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://lacta:lacta@localhost:5432/lacta` | SQLAlchemy / Alembic |
| `REDIS_URL` | `redis://redis:6379/0` | realtime pub/sub |

---

## 7. Test, lint & the RTP gate

```bash
task lint     # ruff + mypy(strict) + import-linter (seam contracts) + biome
task test     # pytest -q  (DB-backed tests need the migrated Postgres from §5)
task rtp      # the RTP subset
```

Run tools directly while iterating on one package:

```bash
uv run ruff check .                 # lint — clean before every commit
uv run mypy engine app verifier     # strict types — from repo root
uv run lint-imports                 # ★ engine purity + layering (the seam gate)
uv run pytest -q                    # full Python suite
```

**The engine-purity grep — the #1 review check (must print nothing):**

```bash
git grep -nE "import (random|secrets|fastapi|sqlalchemy|redis|httpx|asyncio|os|time|datetime|pathlib)|[^.]\bopen\(" -- engine/
```

**RTP-as-CI gate.** A game ships only when its RTP/EV test is wired. Tolerance is a statistical
half-width (`z·√(Var/n)`, z=5) — **never** a flat band you can widen to pass. The heavy sims
(1e7–1e8 trials) carry `@pytest.mark.rtp_heavy` and are excluded from the default run; the
dedicated CI `rtp-gate` job opts back in:

```bash
uv run pytest -q -m rtp_heavy       # the heavy gate (what CI's rtp-gate runs)
```

> **Iron rule:** never weaken a rule or an RTP tolerance to make a check pass — *tune* slot
> strip weights / paytables to hit target, escalate otherwise.

Migrations ship a reversible `down` — prove both directions:

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
```

---

## 8. API surface

Mounted in `app/main.py`; every money-moving call is server-authoritative and idempotent.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | liveness |
| `POST` | `/games/{game_id}/bet` | place a bet / open a round (intent only) |
| `POST` | `/games/{game_id}/action` | advance a stateful round (hit, reveal, hold…) |
| `GET` | `/games/{game_id}/state` | resume a round (redacted public view) |
| `GET` | `/games/originals.crash/state` | Crash round state (status-dependent redaction) |
| `GET` | `/fairness/{bet_id}` | seeds + nonce + derivation + verifier link |
| — (WS) | Crash channel | realtime round actor (Redis fan-out) |
| `POST` | auth router | guest / JWT session |

The client consumes **generated** types from `packages/contracts-ts` (OpenAPI is the contract
seam) — never hand-rolled shapes.

---

## 9. Deployment

This is a **play-money** service; there are **no payment, KYC, or cash-out rails** and
`mode=REAL` is intentionally unwired. The shipping artifact is the container image built from
`app/Dockerfile`.

```mermaid
flowchart LR
  img["app/Dockerfile<br/>multi-stage uv build<br/>non-root uid 10001<br/>uvicorn :8000"]
  img --> svc["API container<br/>(N replicas, stateless)"]
  svc --> pg[("Managed Postgres<br/>ledger · configs · state")]
  svc --> redis[("Managed Redis<br/>realtime pub/sub")]
  cfg["Secrets via platform env<br/>DATABASE_URL · REDIS_URL · JWT keys"] -.-> svc
```

**Image properties (from `app/Dockerfile`):** build context is the repo root; `uv sync --package
app --no-dev --no-editable` produces a self-contained venv; runtime is `python:3.12-slim` as a
non-root user; `CMD uvicorn app.main:app --host 0.0.0.0 --port 8000`.

**Release steps (target platform):**

1. Build & push the image: `docker build -f app/Dockerfile -t <registry>/casino-lacta:<tag> .`
2. Provision managed **Postgres** + **Redis**; inject `DATABASE_URL`, `REDIS_URL`, and JWT
   secrets via **platform env** (never in source or logs).
3. **Run migrations as a release/pre-deploy step:** `alembic upgrade head` (creates schema +
   seeds the `SYSTEM`/house wallet). Idempotent and reversible.
4. Roll out the stateless API replicas; point them at the managed datastores.
5. Health-gate on `GET /health`.

> **Status:** an automated deploy pipeline (the `S41` step) is **not built** and is a
> destructive/paid, human-authorized action — never auto-run. The blueprint's production-readiness
> checklist (`docs/2026-06-27_casino-games_monorepo-blueprint.md` §7) tracks what "best practice"
> still needs beyond the correctness spine. No secrets live in source or logs (platform env only).

---

## 10. CI

`.github/workflows/ci.yml` runs path-filtered jobs:

- **`python`** (when Python paths change): `ruff check` → `mypy engine app verifier` →
  `lint-imports` (seam contracts) → `alembic upgrade head` against a real Postgres service →
  `pytest -q`.
- **`rtp-gate`** (separate, DB-free job): `pytest -q -m rtp_heavy` — the heavy RTP/EV sims.
  A game is considered shippable only once its heavy RTP test is wired under this marker.

Local pre-commit hooks (`lefthook.yml`) mirror the lint gates.

---

## 11. Project status

Greenfield, built by an in-context orchestrator through a numbered step plan (S1–S42) on branch
`casino-games/main`. Durable per-step state lives in
`docs/2026-06-27_casino-games_progress.md` — that file, not this README, is the source of truth
for what is merged.

- ✅ **P1 Spine** — ledger · RNG/fairness · verifier · RTP harness · auth
- ✅ **P2 Instant Originals** — Dice, Limbo, Pocket Dice, Mines, Plinko, HiLo, Keno, Roulette 0–99
- ✅ **P3 Crash** — first realtime (WS actor, server-stamped cash-out, deterministic recovery)
- 🔄 **P4 Slots** — config-driven framework + features done; first machine + RTP gate landing
- ✅ **P5 Table & video poker** — Roulette wheel, Baccarat, Video Poker, Blackjack
- ⏳ **P6 Poker PvP** — highest-risk phase (evaluator → side pots → realtime + redaction → rake)
- ⏳ **P7 Economy / polish / packaging** — faucets, leaderboards, RG, observability, load, deploy

### Docs

| Doc | What |
|---|---|
| `docs/2026-06-27_casino-games_spec_v2.md` | game mechanics, math, contracts (the math source of truth) |
| `docs/2026-06-27_casino-games_build-plan_v2.md` | architecture, repo layout, phases, per-game Definition of Done |
| `docs/2026-06-27_casino-games_monorepo-blueprint.md` | the polyglot monorepo + seam blueprint + production-readiness checklist |
| `docs/2026-06-27_casino-games_implementation-steps.md` | S1–S42 prompts + Verify blocks |
| `docs/2026-06-27_casino-games_progress.md` | durable per-step state (read on resume) |
| `CLAUDE.md` | the iron rules + execution model (authoritative for contributors) |

---

*Play-money only · non-redeemable · `mode=REAL` modeled but never wired.*
