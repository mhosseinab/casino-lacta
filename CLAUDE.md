# Casino (codename lacta) — instructions for Claude Code

Social, **play-money** casino — the GAMES layer only, built server-authoritative. Pure-Python
`engine/` (provably-fair Originals, data-driven slots, table/video poker, realtime Crash + PvP
poker) behind a FastAPI service with a double-entry coin ledger, an open provably-fair verifier,
and RTP-as-a-CI-gate. Python 3 · FastAPI async · Pydantic v2 · SQLAlchemy 2.0 + Alembic ·
Postgres · Redis · Starlette WebSockets.

**The load-bearing boundary: this is play-money `GOLD`, non-redeemable. `mode=REAL` is modeled
but NEVER wired** — no payment rails, no KYC/AML, no cash-out, no prizes, no sweepstakes. Every
"compliance" item is awareness/seam, not built. Don't represent the build as licensable.

## Status — greenfield, orchestrated

The repo root `casino-lacta/` exists and is git-init'd (branch `master`, 0 commits); today it holds
`docs/` (the build kit), `.claude/` (agents + skills), and this `CLAUDE.md`. The code
(`app/`, `engine/`, `verifier/`, `tests/`, `migrations/`) is still greenfield — created by **S1** on
branch `casino-games/main` and grown through **S42**. Build is driven by an in-context orchestrator,
not freehand:

- **What to build, in order:** `docs/2026-06-27_casino-games_implementation-steps.md` — S1–S42,
  each a paste-ready worker prompt + an explicit **Verify** block + the dependency graph.
- **Why / the decided forks:** `docs/2026-06-27_casino-games_plan.md` (§4).
- **How to drive it:** `docs/2026-06-27_casino-games_orchestrator-prompt.md` — worker→reviewer→fix
  loop, branch-per-step, inline human gates.
- **Where you are:** `docs/2026-06-27_casino-games_progress.md` — the DURABLE state table (status |
  attempts | branch | SHA). On start/resume, READ this file — never rely on the transcript.

> The kit names the project `casino-games` (integration branch `casino-games/main`); the workspace
> codename is **lacta**. Game mechanics/math and the repo layout/contracts live in
> `docs/2026-06-27_casino-games_spec_v2.md` + `..._build-plan_v2.md` (both present in `docs/`); the
> per-step prompts point to the §s you need.

## Commands (exist after S1 scaffolds them)

```bash
# Cross-language entry points (Task orchestrates uv + pnpm — see the monorepo blueprint):
task setup                             # bootstrap: uv sync --group dev + pnpm install + lefthook install
task dev                               # docker compose up postgres redis + uvicorn(reload) + vite
task lint                              # ruff + mypy(strict) + import-linter + biome, across both languages
task test                              # pytest + vitest (component tests; no Playwright)
task rtp                               # the heavy RTP-as-CI gate
task contracts                         # regenerate packages/contracts-ts from the API OpenAPI schema

# The underlying tools (run directly when iterating on one package):
ruff check .                           # lint — exit 0 before every commit
mypy engine app verifier               # strict — clean before every commit (run from repo root)
lint-imports                           # import-linter: engine purity + app|verifier→engine layering (SEAM gate)
pytest -q                              # Python suite; per-step Verify blocks run a narrow subset
docker compose up -d postgres redis    # scratch infra for ledger/migration/realtime tests
alembic upgrade head                   # apply migrations on the scratch DB …
alembic downgrade -1                   # … every migration ships a reversible down — prove both
git grep -nE "import (random|secrets|fastapi|sqlalchemy|redis|httpx|asyncio|os|time|datetime|pathlib)|[^.]\bopen\(" -- engine/   # fast ENGINE-PURITY gate:
                                       # MUST return nothing. The #1 review check (import-linter backs it).
pytest -q tests/test_*_rtp.py          # the RTP-as-CI gate — measured RTP ≈ target within tol
```

> **Monorepo:** polyglot — uv (Python: `engine`/`app`/`verifier`) + pnpm (TS: `client` +
> `packages/contracts-ts`), orchestrated by Task, hooks by lefthook. `engine` is its own
> stdlib-only package so purity is a *dependency fact*. Full layout, configs, the contract seam, and
> the production-readiness checklist: `docs/2026-06-27_casino-games_monorepo-blueprint.md`.

CI (`.github/workflows/ci.yml`) runs `ruff check .`, `mypy engine app verifier`, `lint-imports`
(seam contracts), `pytest -q`, plus a separate **rtp-gate** job; a game ships only when its RTP/EV
test is wired into that job.

## Framework references — use Context7, don't guess

Training data drifts; before using a non-trivial API of any third-party framework, pull CURRENT
docs via Context7 (`mcp__claude_ai_Context7__resolve-library-id` → `query-docs`) instead of guessing
syntax/config. The stack to look up:
- **Backend:** FastAPI, Starlette WebSockets, SQLAlchemy 2.0 async, Alembic, Pydantic v2, redis-py
  (asyncio), pytest / pytest-asyncio, Hypothesis.
- **Client (`client/`):** React, Vite, PixiJS, Vitest, React Testing Library.

We deliberately keep **no per-framework skills** — they would rot; Context7 is the live reference.
The project skills (`add-game`, `add-game-ui`, `rtp-harness`) own the casino-specific recipes
(engine purity, the bet loop, RTP tuning, the thin-renderer boundary), not framework syntax.

## Map

| Path | What lives there |
|---|---|
| `engine/` | **uv member — PURE Python outcome logic at `engine/src/engine/`; stdlib-only deps.** `rng.py` (HMAC-SHA256 seeded stream), `fairness.py` (commit/reveal/verify), `money.py` (minor-units math), `types.py` (game protocols), `registry.py`, `games/` (Originals + `_curve.py`), `slots/`, `table/`, `poker/`, `cards/evaluator.py`. No framework, no IO, no clock, no `random` — purity is a dependency fact. |
| `app/` | **uv member — all framework/DB/IO at `app/src/app/`** (depends on `engine`). `main.py` (FastAPI), `db/models.py` (SQLAlchemy), `wallet/ledger.py` (double-entry), `auth/`, `api/` (generic game routers), `games/bet_loop.py` (the shared place-bet flow), `ws/{crash,poker}.py` (realtime actors), `economy/`, `rg/` (responsible-gaming gate). Seed *generation* (`secrets` CSPRNG) lives here, never in `engine/`. |
| `verifier/` | **uv member at `verifier/src/verifier/`** (depends on `engine` ONLY) + a static page. **Imports `engine/` verbatim** — server result MUST equal verifier result. Never a re-implementation. |
| `tests/` | RTP/EV gates, determinism, verifier-parity, rules property tests, side-pot conservation, ledger concurrency, load. `rtp_harness.py` is the shared `run_rtp(game, input, n, target, tol)`. |
| `migrations/` | Alembic — every migration reversible. |
| `client/` | **Thin web renderer (Vite + React + TS, canvas/PixiJS) — presentation only, optional.** Renders server-authoritative state; NEVER decides an outcome or balance. Component tests via Vitest + React Testing Library (no Playwright at this stage). |
| `docs/` | The orchestration kit — see Status above + Docs index below. |

## Iron rules (each one paid for in debugging time)

**★ Engine purity — the load-bearing constraint.** `engine/` outcome logic imports only stdlib
(`hashlib`, `hmac`, `math`, `dataclasses`, `typing`). NO `fastapi`/`sqlalchemy`/`redis`/network/
file/wall-clock, and **never `random`** — entropy enters ONLY via the seeded `RngStream`. This is
what makes the verifier reuse `engine/` verbatim (server == verifier by construction) and every
outcome deterministic + unit-testable. The grep above guards it; a reviewer fails the step on any
hit. Seed *generation* (CSPRNG via `secrets`) lives in `app/`, never `engine/`.

- **Money is integer minor units everywhere.** `BIGINT` columns; reject floats at the API
  boundary; format only at display. `engine/src/engine/money.py#apply_multiplier` rounds DOWN (floor) —
  the truncated fraction is house margin — and `cap(payout, maxWin)` applies AFTER rounding. No float
  is ever stored or settled.
- **Server-authoritative.** The client sends intent; the SERVER decides every outcome and balance.
  The client is hostile and is a thin renderer, never the deliverable. Crash cash-out multiplier
  is server-stamped at receipt; poker holds state server-side with per-seat redaction.
- **Double-entry ledger only.** Credits move solely by writing an append-only balanced two-row
  `LedgerEntry` set sharing one `txnId`, whose counterparty is the reserved SYSTEM/house account
  (`User(id="SYSTEM")` + a house `Wallet` per currency) so player + house sum to zero;
  `wallet.balance` is a reconciled PROJECTION, never mutated directly. Two invariants:
  `reconcile(walletId)` must satisfy `balance == sum(entries)` — to the cent, under concurrency — AND
  system-wide `Σ(all entries) == 0`.
- **Idempotency.** Every state-changing op carries `idempotencyKey = hash(betId, opType)`; a
  re-send returns the ORIGINAL result and never re-applies. Single-debit / single-credit per bet.
- **Determinism.** An outcome is a pure function of `(serverSeed, clientSeed, nonce, input)`.
  Same inputs → same stream → same result, always.
- **TDD — no production code without a failing test first.** RED → GREEN → REFACTOR on every step:
  write the test (RTP/EV, determinism, parity, rules, ledger, redaction, or RTL component test),
  watch it fail for the RIGHT reason, then write the simplest code to pass. A step's **Verify block
  is the test** — the worker pastes ACTUAL output; a passing-on-first-write test proves nothing.
  Bug fixes start with a failing reproduction. NEVER weaken, skip, or vacuously assert a test to go
  green — escalate. (Throwaway math/table exploration is exempt, but if you keep the result, test
  it first.)
- **Provable fairness for Originals.** commit–reveal: `serverSeedHash` committed before the bet,
  revealed on rotation; `GET /fairness/{betId}` exposes seeds/nonce/derivation + verifier link.
- **Never weaken a rule or an RTP tolerance to make a check pass — escalate.** Tune slot strip
  weights / paytables to hit target RTP; NEVER clamp outcomes or loosen a tolerance.
- **Realtime integrity is where bugs concentrate** (Crash cash-out, poker state). One authoritative
  round/table actor per partition; Redis pub/sub fan-out; recovery replays from the committed seed.
  Poker: the server NEVER sends a seat any card it shouldn't see (no hole-card leakage — tested).
- **Per commit:** `ruff check .` + `mypy engine app verifier` clean; stage files BY NAME; Conventional
  Commits; don't bypass hooks; never force-push; English only; update the doc that describes a
  behavior in the SAME commit as the change.

## Design principles (SOLID · DRY · KISS — built for seams)

Everything is designed so a swappable dependency or policy is a **config/adapter change, not a
rewrite**. The *seams* — where one implementation sits behind a stable, consumer-shaped interface —
ARE the architecture. Honor them on both sides. (Detail + the monorepo that enforces them:
`docs/2026-06-27_casino-games_monorepo-blueprint.md`.)

The seams in this system:
- **RNG source** → `engine.RngStream` protocol; outcome logic depends on the abstraction, never a
  concrete generator. Swapping entropy touches one factory in `app/`.
- **Game** → `InstantGame` / `StatefulGame` protocols + `engine/src/engine/registry.py`. A new game is a new
  conformance + a registry entry; the bet loop, ledger, and API never change (OCP).
- **Verifier == server** → the verifier imports `engine/` verbatim — ONE outcome implementation,
  reused, never duplicated. This is DRY at its most load-bearing (duplicate it and the verifier
  stops proving the server).
- **Persistence / state** → Postgres behind the wallet/repository services; Redis behind a
  state-store interface. `engine/` knows neither (no IO/DB seam crosses into it).
- **Money / economy policy** → `engine/src/engine/money.py` is the single owner of rounding/caps; rake, RG
  limits, RTP targets, model ids are CONFIG, never code literals. DB `GameConfig`/`GameLimit` are
  authoritative at runtime (seeded via `seed_configs()`); engine `registry.py` provides the
  defaults/seed values.
- **Transport** → REST + WS gateways in `app/`; realtime actor logic is transport-agnostic.
- **Client ↔ server contract** → the OpenAPI schema is the seam; the TS client consumes GENERATED
  types (`packages/contracts-ts`), never hand-rolled shapes. One contract, two languages.

Applying the principles as design pressure (not ceremony):
- **SRP** — `engine/` decides outcomes, `app/` does IO/persistence/transport, `verifier/` proves,
  `client/` renders. A file mixing two of these is mis-placed.
- **DIP** — depend on protocols (`RngStream`, `InstantGame`, repository interfaces), not concretes.
  Enforced structurally: `engine` is its own package whose deps are **stdlib-only**, so it CANNOT
  import a framework (purity by construction), and an **import-linter** contract guards the layering
  (`app|verifier → engine`, never the reverse).
- **OCP** — extend by adding a conformance/registry entry or config, not by editing the spine.
- **ISP** — interfaces stay small and consumer-shaped (`RngStream.next()`, not an RNG god-object).
- **DRY** — one owner per concept (one outcome impl, one ledger, one money-rounding rule, one input
  fence). Duplication of *meaning* is the smell; coincidental similarity is fine — don't over-abstract.
- **KISS / YAGNI** — the simplest thing that satisfies the step. `mode=REAL`, the Go Crash sidecar,
  and provably-fair-for-slots are documented SEAMS, not built. Don't earn the coupling back.

Reviewers judge seam adherence alongside correctness; `import-linter` + the purity grep enforce it
mechanically.

## Execution model (the orchestrator loop)

- **Branch-per-step on `casino-games/main`.** Each step on `casino-games/S<N>` (worktree if
  supported); a fresh worker subagent implements ONLY that step, runs its Verify block and pastes
  ACTUAL output; a fresh reviewer subagent judges the diff (`VERDICT: PASS` / `CHANGES_REQUESTED`);
  merge on PASS + green Verify; cap 3 fix cycles → else mark `blocked` and stop.
- **Dependency DAG, not a flat list.** A step is eligible when prerequisites are `passed`. Dispatch
  disjoint eligible steps CONCURRENTLY; serialize steps that share files. Parallel sets: `{S4,S5,S8}`,
  `{S10..S17}` (after the S9 gate), `{S25,S26,S27,S28}`, `{S36,S37,S38,S39}`.
- **Two CARRY-FORWARDs — reuse, never duplicate:** `engine/src/engine/games/_curve.py` (S10 → reused by Crash
  S18); `engine/src/engine/cards/evaluator.py` (S28 5-card core → extended to best-5-of-7 by poker S29).
- **Security-grade review on the trust-boundary diffs:** S3 (ledger), S4 (RNG/fairness), S8 (auth),
  S19/S20 (cash-out), S32 (hole-card redaction), S33 (rake/ledger), S35 (anti-collusion), S41
  (deploy/secrets) — route to `/security-review`; everything else `/code-review`.
- **Inline gates — STOP and require an explicit human "go":**
  - **S9** REVIEW GATE — after the Dice vertical slice passes, before scaling to the other Originals
    (the cheapest moment to correct engine purity / bet-loop shape / fairness).
  - **S40** HUMAN SIGN-OFF — load test (needs live infra; record a written go/no-go). Never in an
    unattended loop.
  - **S41** DESTRUCTIVE/PAID — deploy. Never auto-run; the human authorizes/performs billing + account
    actions and secret handling.

### Agents & skills (`.claude/`)

Project-scoped config tailored to the detected stack and iron rules. The orchestrator routes each
diff to the matching reviewer; workers invoke the skills for repeated tasks. All reviewers are
**read-only** (return `VERDICT: PASS`/`CHANGES_REQUESTED` + `file:line` issues; they never edit) and
all check **test-first / TDD discipline** — that the Verify tests are real, fail without the change,
and weren't weakened.

- **Agents** — `engine-purity-reviewer` (every `engine/` diff: purity, determinism, minor units,
  RTP math, parity); `ledger-security-reviewer` (security-grade: ledger/wallet/auth/economy/rake —
  S3, S6, S8, S33, S36, S37); `fairness-rng-reviewer` (security-grade: RNG/commit-reveal/verifier —
  S4, S7, parity tests); `realtime-integrity-reviewer` (security-grade: Crash/poker WS — no
  double-credit, server-authoritative cash-out, hole-card redaction — S18–S20, S32–S35);
  `frontend-renderer-reviewer` (`client/`: thin-renderer boundary, minor-units display, cosmetic
  animation, fairness/RG surfaced — no Playwright).
- **Security-grade step set** (route to a security-grade reviewer / `/security-review`):
  S3, S4, S6, S7, S8, S18, S19, S20, S32, S33, S34, S35, S41.
- **Skills** — `add-game` (the per-game backend recipe, TDD-first); `rtp-harness` (author/tune the
  RTP-as-CI gate without weakening tolerance); `add-game-ui` (the thin-client view recipe,
  Vitest/RTL component tests). The global `/code-review` + `/security-review` remain the fallback.

## Phases (each ends demoable)

`P1 Spine (S1–S8)` ledger + RNG/fairness + verifier + RTP harness + auth → `P2 Instant Originals
(S9–S17)` Dice⛔gate, Limbo, Pocket Dice, Mines, Plinko, HiLo, Keno, Roulette 0–99 → `P3 Crash
(S18–S20)` first realtime → `P4 Slots (S21–S24)` data-driven framework + machines → `P5 Table &
video poker (S25–S28)` → `P6 Poker PvP (S29–S35)` ⚠️ highest risk; evaluator → side pots → table
reducer → realtime+redaction → matchmaking/rake → timers → anti-collusion → `P7 Economy/polish/
packaging (S36–S42)` faucets, leaderboards, RG, observability, ⛔load test, ⛔deploy, README.
P6 (poker) can be cut entirely without affecting P1–P5 value.

## Privacy / security

- The client is hostile: the server decides every outcome and balance (enforced by engine purity +
  server-authoritative checks + per-seat poker redaction).
- PII is minimal — auth identifiers only (play-money guest/JWT). No payment data, no KYC. Honor
  account data-deletion. No secrets in source or logs (platform env only).

## Docs index

- `docs/2026-06-27_casino-games_implementation-steps.md` — S1–S42 prompts + Verify blocks (the script)
- `docs/2026-06-27_casino-games_plan.md` — the *why* + decided forks (§4)
- `docs/2026-06-27_casino-games_orchestrator-prompt.md` — the *driver* (loop, gates, review routing)
- `docs/2026-06-27_casino-games_progress.md` — durable S1–S42 state table (read on every start/resume)
- `docs/2026-06-27_casino-games_spec_v2.md` — game mechanics, math, contracts (§2.2 bet object,
  §2.3 fairness, §2.9 schema, §2.10 API envelope, Appendix A per-game math). The math source of truth.
- `docs/2026-06-27_casino-games_build-plan_v2.md` — architecture, repo layout, shared contracts (§3),
  phases M0–M7, per-game Definition of Done (§5).
- `docs/2026-06-27_casino-games_monorepo-blueprint.md` — the **polyglot monorepo + seam blueprint**
  S1 scaffolds against (uv + pnpm + Task + lefthook + import-linter), and the **production-readiness
  checklist** (§7 — what "best practice" still needs beyond the correctness spine).

When you change behavior, update the doc that describes it in the same commit; when a step lands,
record its status + SHA in the progress file before advancing.
