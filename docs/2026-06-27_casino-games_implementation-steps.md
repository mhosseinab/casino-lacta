---
type: steps
project: casino-games
tags: [casino, igaming, migration-orchestration, fastapi, implementation-steps, dev]
status: READY TO EXECUTE, gaps-patched 2026-06-27
slug: casino-games
date: 2026-06-27
companion_plan: 2026-06-27_casino-games_plan.md
driver: 2026-06-27_casino-games_orchestrator-prompt.md
tracker: 2026-06-27_casino-games_progress.md
---

# Casino Games — Step-by-Step Implementation

**Status: READY TO EXECUTE (2026-06-27).** Companion to [[2026-06-27_casino-games_plan.md]] (the *why*). This is the *how* — ordered, **standalone, independently verifiable** steps S1–S42, each a paste-ready worker prompt + an explicit Verify block. Mechanics live in [[2026-06-27_casino-games_spec_v2]]; phases/DoD in [[2026-06-27_casino-games_build-plan_v2]].

## How to use this
Run steps **in order, respecting the dependency graph**. Each is self-contained: land it, run its Verify, commit it, then advance. Paste a step's fenced **prompt** into a worker subagent; it must run the **Verify** block and paste actual output. Nothing destructive ships until P7; everything before is additive and play-money-only.

## Decisions baked in (final, 2026-06-27 — from plan §4)
- **Money:** social play-money `GOLD`, non-redeemable; `mode=REAL` modeled but never wired (§4.1).
- **Stack:** Python/FastAPI async, Postgres (SQLAlchemy 2.0 + Alembic), Redis, Starlette WS (§4.2).
- **Engine purity:** `engine/` has no `fastapi`/`sqlalchemy`/IO/wall-clock/`random` in outcome logic (§4.3).
- **Fairness:** provably-fair commit–reveal + verifier for Originals; server-authoritative CSPRNG + audit elsewhere (§4.4).
- **Scope:** full target (Originals + slots + table/video poker + PvP poker) (§4.5).
- **Correctness:** RTP-as-CI gate for every game (§4.6).
- **Execution:** branch per step on `casino-games/main`; Conventional Commits; orchestrator driver (§4.7).

## Project rules every prompt must respect (stated once)
- **Engine purity:** outcome logic in `engine/` imports only stdlib (`hashlib`, `hmac`, `math`, `dataclasses`, `typing`). No `fastapi`, `sqlalchemy`, `redis`, `httpx`, `asyncio`, `os`, `time`, `datetime`, `pathlib`, file `open(`, network, or wall-clock access, and **never `random`** — entropy enters only via the seeded `RngStream`. Seed *generation* (CSPRNG via `secrets`) lives in `app/`, not `engine/`. The canonical purity grep (used in every Verify block) is `git grep -nE "import (random|secrets|fastapi|sqlalchemy|redis|httpx|asyncio|os|time|datetime|pathlib)|[^.]\bopen\(" -- engine/` and MUST return nothing.
- **Money is integer minor units** everywhere; reject floats at the API boundary; format only at display.
- **Server-authoritative:** the client sends intent; the server decides every outcome and balance.
- **Double-entry ledger only:** credits move solely by writing an append-only `LedgerEntry`; `wallet.balance` is a reconciled projection, never mutated directly.
- **Idempotency:** every state-changing op carries `idempotencyKey = hash(betId, opType)`; re-sends return the original result.
- **Determinism:** an outcome is a pure function of `(serverSeed, clientSeed, nonce, input)`.
- **Verifier parity:** the verifier imports `engine/` verbatim; server result must equal verifier result.
- **Never weaken a rule or an RTP tolerance to make a check pass** — escalate instead.
- **Per commit:** `ruff check .` and `mypy engine app verifier` clean; stage files by name; Conventional Commits; don't bypass hooks; English only; update docs in the same commit as behavior.

## Dependency graph (quick view)
```
P1 Spine        S1 → S2 → S3 ;  S1 → S4 ;  S1 → S5 ;  {S2} → S8 ;  {S3,S4,S5} → S6 → S7
P2 Originals    {S6,S7} → S9(REVIEW GATE) → {S10,S11,S12,S13,S14,S15,S16,S17}   # parallel after S9
P3 Crash        S10 → S18 → S19 → S20
P4 Slots        {S6,S7} → S21 → S22 → S23 → S24                                  # independent of P2/P3
P5 Table        {S6,S7} → {S25, S26, S27, S28}                                   # parallel
P6 Poker        S6 → S29 → S30 → S31 → S32(also needs S18) → {S33(+S3), S34} → S35
P7 Polish       S3→S36 ; S3→S37 ; S6→S38 ; S6→S39 ; S20→S40 (+S35 if poker built)[SIGN-OFF] → S41[DEPLOY/GATED] → S42
```
Parallelizable sets (disjoint files): {S4,S5,S8}; {S10..S17}; {S25,S26,S27,S28}; {S36,S37,S38,S39}.
**Shared-file serialization:** `engine/src/engine/cards/evaluator.py` is touched by both **S28** (video poker, 5-card core) and **S29** (poker, best-5-of-7). SOFT constraint — if both are in scope, serialize them (shared file); whichever runs first CREATES the evaluator, the other EXTENDS it (never duplicate the ranking).

---

# Phase 1 — Spine (P1) — foundations, ships nothing user-facing

### S1 — Scaffold the monorepo, tooling, CI, FastAPI health
**Goal:** the existing repo root (`casino-lacta/`) becomes a **polyglot monorepo** (uv + pnpm, Task-orchestrated) that builds, lints, type-checks, runs, and serves `GET /health` → 200; CI runs the empty gates green; the engine-purity seam is structurally enforced.
**Depends on:** none.
**PRE-S1 prereq:** run `task doctor` first — the toolchain must be present (uv, pnpm, node, docker, Task, lefthook). Resolve any missing tool before scaffolding.

```
The repo ALREADY EXISTS at the workspace root `casino-lacta/` — already `git init`-ed on branch `master` with 0 commits (only docs/ + .claude/ + CLAUDE.md present; all code is greenfield). Do NOT create a nested project directory and do NOT re-init git. Create branch casino-games/main off the first commit and make that first commit there. Scaffold per 2026-06-27_casino-games_monorepo-blueprint.md §3 (which refines build-plan_v2 §2 into a polyglot monorepo) into the existing repo root.
Scaffold the workspace:
- Root uv workspace pyproject.toml with members = ["engine", "app", "verifier"]; shared [tool.ruff] + [tool.mypy] (strict) config; [dependency-groups] dev = [pytest, pytest-asyncio, hypothesis, httpx, ruff, mypy, import-linter].
- engine/ uv member: engine/pyproject.toml with NO framework deps (stdlib only — this is what makes purity a dependency fact); package at engine/src/engine/__init__.py.
- app/ uv member: app/pyproject.toml depends on engine ({ workspace = true }) + fastapi, uvicorn[standard], pydantic>=2, sqlalchemy[asyncio]>=2, alembic, redis; FastAPI app at app/src/app/main.py exposing GET /health -> {"status":"ok"}.
- verifier/ uv member: verifier/pyproject.toml depends on engine ({ workspace = true }) ONLY; package at verifier/src/verifier/__init__.py.
- tests/ at repo root (empty); migrations/ + alembic.ini at root.
- .importlinter with the two contracts from the blueprint §5 (engine-is-pure; app|verifier → engine layering).
- pnpm-workspace.yaml + root package.json (lefthook, biome) — client/ and packages/contracts-ts/ may be empty placeholders for now.
- Taskfile.yml (setup/dev/lint/test entry points), lefthook.yml, biome.json, .editorconfig, .dockerignore, docker-compose.yml (services: api, postgres:16, redis:7).
- .github/workflows/ci.yml running: ruff check ., mypy engine app verifier, lint-imports, pytest -q (paths-filter py/ts per the blueprint).
- Placeholder tests/test_health.py using httpx ASGITransport asserting /health == 200.
Do NOT add any game logic. Honor: English only; engine/ stays import-pure (nothing yet). Conventional Commits ("chore: scaffold monorepo + ci").
```
**Verify:** `ruff check .` exit 0; `mypy engine app verifier` clean; `lint-imports` passes (purity + layering contracts); `pytest -q` green incl. `test_health`; `docker compose config` valid; `uv sync` resolves the three members against one root `.venv`; diff is scaffold-only.

---

### S2 — Data model + Alembic migrations
**Goal:** all games-layer tables from spec_v2 §2.9 exist via a reversible migration on a scratch Postgres.
**Depends on:** S1.

```
Implement SQLAlchemy 2.0 async models + an Alembic migration for the spec_v2 §2.9 schema: User, Wallet(balanceMinor), LedgerEntry(append-only, double-entry), GameRound, Bet, ServerSeed, ClientSeed, NonceCounter, GameConfig(versioned), GameLimit, AuditEvent (PokerTable + Jackpot may be stubbed/empty for now). Money columns are BIGINT minor units. Put models in app/src/app/db/models.py; do NOT put SQLAlchemy in engine/.
Reserved SYSTEM (house) account: seed a User(id="SYSTEM") plus one house Wallet per currency (userId=SYSTEM) so every double-entry has a counterparty (S3 books player+house rows sharing one txnId).
Config seeder: include a `seed_configs()` Alembic DATA migration (run in this step) that seeds the authoritative DB tables — GameConfig (versioned) and GameLimit — from the engine registry defaults. CONFIG AUTHORITY: DB GameConfig/GameLimit are authoritative at runtime; engine registry.py supplies defaults/seed values; each later game step adds its own GameConfig/GameLimit row. `configVersion` = GameConfig.version (stamped into AuditEvent by the bet loop).
Provide a working down-migration. Rule: every migration ships a reversible down. Run the up migration on a scratch DB (docker compose up -d postgres) and paste output, then the down reverting cleanly.
```
**Verify:** `alembic upgrade head` applies on a scratch DB (paste), seeding the SYSTEM user + house wallet(s) and the GameConfig/GameLimit rows; `alembic downgrade -1` reverts cleanly; `mypy app` clean; `git grep -n "import sqlalchemy" -- engine/` returns nothing.

---

### S3 — Double-entry ledger service (idempotent) + reconciliation
**Goal:** `debit`/`credit`/`rollback`/`grant` move credits only via append-only double-entry `LedgerEntry`, are idempotent, and a reconciliation job proves `wallet.balance == sum(entries)`.
**Depends on:** S2.

```
Implement app/src/app/wallet/ledger.py: async debit(walletId, amountMinor, idempotencyKey, ref), credit(...), rollback(opRef), grant(...). Each writes a BALANCED two-row LedgerEntry set in ONE DB transaction (the player wallet row + the SYSTEM/house wallet row for that currency, sharing one txnId; the two rows sum to zero) and updates the wallet projection. Every debit/credit/grant/rollback/rake books its house counterparty — credits never enter or leave the system without the house side. Idempotency: a repeated idempotencyKey returns the original result, never re-applies. debit fails on insufficient funds (typed error). Add reconcile(walletId) asserting per-wallet `balance == Σ entries`, AND a system-wide invariant `Σ(all entries) == 0`. Rules: minor units only; never mutate balance outside a ledger write. Tests in tests/test_ledger.py: idempotent debit (double-send applied once), no negative balance, credit/rollback, a concurrency test issuing N parallel debits that must not overspend, AND a system-wide-conservation test asserting Σ(all LedgerEntry amounts) == 0 after a mixed series of grants/debits/credits/rollbacks. Run pytest and paste output.
```
**Verify:** `pytest -q tests/test_ledger.py` green incl. idempotency + concurrency + system-wide `Σ(all entries)==0` cases; per-wallet `reconcile()` returns balanced (`balance == Σ entries`); `mypy app` clean.

---

### S4 — RNG + provably-fair module (pure engine)
**Goal:** `engine/src/engine/rng.py` + `engine/src/engine/fairness.py` implement the HMAC-SHA256 seeded stream and commit/reveal/verify, deterministically and import-pure.
**Depends on:** S1.

```
Implement engine/src/engine/rng.py: create_rng(server_seed: bytes, client_seed: str, nonce: int) -> RngStream where next() yields uniform floats in [0,1) from HMAC_SHA256(server_seed, f"{client_seed}:{nonce}:{cursor}") (4 bytes -> uint32 / 2**32, bump cursor). Implement engine/src/engine/fairness.py: commit(server_seed)->sha256 hex, and verify(server_seed, client_seed, nonce, derive_fn) recomputing an outcome. Pure stdlib only (hashlib, hmac). NO secrets/random here (seed generation lives in app/). Tests in tests/test_rng.py: determinism (same inputs -> same stream), uniformity smoke (mean ~0.5 over 1e5), commit reproducibility. Run pytest and paste output.
```
**Verify:** `pytest -q tests/test_rng.py` green; `python -c "import engine.rng, engine.fairness"` works without app/DB; `git grep -nE "import (random|secrets|fastapi|sqlalchemy|redis|httpx|asyncio|os|time|datetime|pathlib)|[^.]\bopen\(" -- engine/` returns nothing.

---

### S5 — Engine contracts, money math, registry (pure)
**Goal:** `engine/src/engine/types.py` (InstantGame/StatefulGame protocols, Outcome, GameConfig), `engine/src/engine/money.py` (minor-units, apply_multiplier, caps), `engine/src/engine/registry.py` exist and are import-pure.
**Depends on:** S1.

```
Implement engine/src/engine/types.py with the Protocols from build-plan_v2 §3 (RngStream, GameConfig, Outcome, InstantGame.play, StatefulGame.init/step). Implement engine/src/engine/money.py: apply_multiplier(stakeMinor:int, multiplier:float)->int that rounds DOWN (floor) — the truncated fractional minor unit is house margin — and a cap(payoutMinor, maxWinMinor) helper applied AFTER rounding (payout == maxWin when the floored product exceeds it); integer minor units only, no floats stored. Implement engine/src/engine/registry.py: a mapping id->(module, default GameConfig) (empty for now). Tests tests/test_money.py: apply_multiplier floor rounding (truncated fraction kept by house), cap enforcement after rounding, no float leakage. Pure stdlib only. Run pytest and paste output.
```
**Verify:** `pytest -q tests/test_money.py` green; `python -c "import engine.types, engine.money, engine.registry"` clean; engine purity grep (`git grep -nE "import (random|secrets|fastapi|sqlalchemy|redis|httpx|asyncio|os|time|datetime|pathlib)|[^.]\bopen\(" -- engine/`) clean.

---

### S6 — Shared bet loop + game API envelope + limits + RG hook
**Goal:** a generic place-bet→debit→run-engine→credit→audit flow exists behind `POST /games/{id}/bet|action`, `GET /games/{id}/state`, with per-currency limit validation and a `can_bet` RG pre-bet gate; provable via a stub game.
**Depends on:** S3, S4, S5.

```
Implement app/src/app/games/bet_loop.py: given a registered game + validated input, validate limits (GameLimit), call RG can_bet() (stub returning allow), debit via ledger, run engine play()/init()+step(), apply_multiplier, credit on win, write AuditEvent linking seed+nonce+input+configVersion (where `configVersion` = the runtime DB `GameConfig.version`, NOT an engine literal), return the spec_v2 §2.2 bet object. This shared loop OWNS the one-active-(user,game)-round guard + per-(user,game) action serialization (ONE-ACTIVE-ROUND): stateful games (Mines S12, HiLo S14, Blackjack S25, Baccarat S27, …) USE this guard, never re-implement it. Wire generic routers app/src/app/api/games.py for /bet, /action, /state. Register a trivial "stub.coinflip" engine game (50/50, 1.98x) to exercise the loop end-to-end. Note: `stub.coinflip` here is a deliberately distinct test fixture from the user-facing `originals.coinflip` shipped in S17 — they coexist by design. Rules: single-debit/single-credit; idempotency; server-authoritative. Test tests/test_bet_loop.py placing a stub bet end-to-end (ledger moved once, audit written with configVersion from GameConfig). Run pytest and paste output.
```
**Verify:** `pytest -q tests/test_bet_loop.py` green (stub bet debits+credits once, audit row written); `mypy engine app verifier` clean; `ruff check .` exit 0.

---

### S7 — Verifier + RTP-harness + CI RTP gate
**Goal:** an open `verifier/` recomputes any bet from its seeds using `engine/` verbatim, and a reusable RTP-harness asserts `measured RTP ≈ target` — wired as a CI job.
**Depends on:** S4, S5, S6.

```
Implement the verifier/ uv member (a Python reference at verifier/src/verifier/ that imports engine/ functions directly — no re-implementation — plus a small static page at verifier/web/) that, given (serverSeed, clientSeed, nonce, input, gameId), reproduces the outcome. Support TWO derivation signatures: the per-USER Originals signature (clientSeed = the player's client seed, nonce = the player's per-(user,game) bet nonce) and a `round` mode for realtime Crash (per-ROUND seed: clientSeed = roundId public salt, nonce = roundNumber, cursor = 0 — see S18). Add GET /fairness/{betId} returning seeds(+revealed)/nonce/derivation/verifier link. Implement tests/rtp_harness.py: run_rtp(game, input, n, target, tol) running n trials over a seeded stream and asserting |rtp-target|<=tol. The harness MUST be invoked with limits set so the maxWin cap does NOT bind (pick stake/maxWin/maxMultiplier so top_multiplier*stake <= maxWin) — the gate measures UNCAPPED math; cap behavior is a separate unit test. Default tolerance is a CLT/binomial confidence half-width for n, not a flat ±0.5%. RTP COUNTS: a fast subset (~1e6 trials) runs on PR/`pytest`; heavy 1e7–1e8 runs nightly/manual in the dedicated rtp-gate job. numpy-vectorized sims are allowed in tests/ ONLY (never engine/). Add a CI job "rtp-gate" invoking the heavy RTP tests. Prove it on stub.coinflip (target 0.99). Rules: verifier reuses engine/ (parity), never a copy; never widen a tolerance to pass. Run pytest and paste output.
```
**Verify:** `pytest -q tests/test_rtp_stub.py` green (coinflip RTP 0.99 ± tol over ≥1e6); verifier reproduces a sample bet (paste); CI shows the rtp-gate job.

---

### S8 — Auth (play-money sessions)
**Goal:** guest + JWT sessions issue a wallet; endpoints require a valid session; no PII beyond an identifier.
**Depends on:** S2.

```
Implement app/src/app/auth/: guest session creation (issues a User + GOLD Wallet, then CALLS the S36 faucet welcome-grant — do NOT issue an ad-hoc grant here) and JWT access/refresh; a FastAPI dependency that resolves the current user/wallet. The welcome grant is OWNED by the S36 faucet module and is idempotent per user (key hash(userId,'welcome')); until S36 lands, call a thin welcome-grant seam that S36 implements/replaces (single owner, no duplicate grant logic). Keep it minimal (play-money). Rule: minor units; grant only via ledger; no payment/KYC. Test tests/test_auth.py: guest signup -> session -> wallet exists with the welcome balance from a single idempotent ledger grant (re-trigger does not double-grant). Run pytest and paste output.
```
**Verify:** `pytest -q tests/test_auth.py` green; protected route 401 without token, 200 with; welcome balance traces to a `grant` LedgerEntry.

---

# Phase 2 — Instant Originals (P2) — each: pure engine fn + endpoint + RTP + determinism + verifier parity

### S9 — Dice (vertical slice) ⟵ REVIEW GATE
**Goal:** Dice is playable end-to-end (engine→bet loop→API→verifier), with RTP and determinism tests green. This proves the whole spine; **stop for human review after this step.**
**Depends on:** S6, S7.

```
Implement engine/src/engine/games/dice.py per spec_v2 §A.1: play(input{target,direction}, rng, cfg) -> Outcome where roll=floor(rng.next()*10000)/100, UNDER wins if roll<target with p=target/100, multiplier=(1-edge)/p. Register it; wire POST /games/originals.dice/bet via the shared bet loop. Add tests/test_dice_rtp.py (RTP over 1e7 rolls at several targets within 1-edge ± 0.5% using rtp_harness), tests/test_dice_determinism.py (same seed/nonce/input -> same roll), and a verifier-parity test (verifier reproduces the bet). Rules: engine purity; determinism; minor units; cap payout at maxWin. Run all three test files and paste output.
```
**Verify:** `pytest -q tests/test_dice_rtp.py tests/test_dice_determinism.py tests/test_dice_parity.py` green; RTP within ±0.5% at targets {2,50,98}; verifier reproduces a sample bet. **Then STOP — human review of the slice before scaling.**

---

### S10 — Limbo (defines the Crash generator)
**Goal:** Limbo playable; the `crash_point(f,edge)` generator lives in `engine/` reusable by Crash.
**Depends on:** S9.

```
Implement engine/src/engine/games/limbo.py per spec_v2 §A.3 using a shared helper crash_point(f, edge)=max(1.00, floor((1-edge)/(1-f)*100)/100) placed in engine/src/engine/games/_curve.py (Crash S18 will reuse it — CARRY-FORWARD). play sets generated X; win if X>=target, payout=target. Register + wire endpoint. Tests: tail probability P(X>=x)=(1-edge)/x within tolerance over 1e6; determinism; verifier parity. Run and paste output.
```
**Verify:** `pytest -q tests/test_limbo_*.py` green (tail prob + RTP within tol); `engine/src/engine/games/_curve.py` exists and is imported by limbo; engine purity grep clean.

---

### S11 — Pocket Dice
**Goal:** 2d6 sum game playable; pmf-exact RTP.
**Depends on:** S9.

```
Implement engine/src/engine/games/pocketdice.py per spec_v2 §A.2 (two dice = floor(rng.next()*6)+1; sum 2-12; OVER/UNDER target; multiplier=(1-edge)/p from the 2d6 pmf). Disallow OVER 12 / UNDER 2. Register + endpoint. Tests: empirical pmf matches 2d6; per-target RTP 0.99 within tol; determinism; parity. Run and paste output.
```
**Verify:** `pytest -q tests/test_pocketdice_*.py` green; pmf frequencies match within tol; per-target RTP within ±0.5%.

---

### S12 — Mines (stateful)
**Goal:** Mines playable with reveal/cashout; EV exact per (M,k); layout committed at round start.
**Depends on:** S9.

```
Implement engine/src/engine/games/mines.py per spec_v2 §A.6 as a StatefulGame: init() samples M mine positions via sampleWithoutReplacement(25,M) from the rng AT ROUND START (committed); step(reveal/cashout) returns multiplier C(25,k)/C(25-M,k)*(1-edge). Wire /bet (single debit) + /action (reveal, cashout) USING the shared S6 one-active-(user,game)-round guard + action serialization (do NOT re-implement it here). Tests: EV=0.99 across M in {1,3,5}, k in {1..5}; layout determinism + verifier parity; cannot reveal post-terminal; single-debit; the shared guard rejects a second concurrent round. Run and paste output.
```
**Verify:** `pytest -q tests/test_mines_*.py` green (EV exact; single-debit; no post-terminal reveal); determinism + parity pass.

---

### S13 — Plinko (tuned table)
**Goal:** Plinko playable; published multiplier table tuned so post-rounding RTP ≤ ±0.2% of target.
**Depends on:** S9.

```
Implement engine/src/engine/games/plinko.py per spec_v2 §A.5: rightBounces=sum(rng.next()<0.5 for R rows); bin index -> multiplier from a per-(rows,risk) table you TUNE so sum P(i)*m_i = target after 2-dp rounding (optimize the table; do not clamp outcomes). Rendered path must end in the server bin. Register + endpoint. Tests: bin frequencies match Binomial(R,1/2); published-table RTP within ±0.2% after rounding for every (rows,risk); determinism; parity. Run and paste output.
```
**Verify:** `pytest -q tests/test_plinko_*.py` green; per-(rows,risk) RTP within ±0.2% post-rounding; bin frequencies match.

---

### S14 — HiLo (stateful)
**Goal:** HiLo playable; per-rank step multipliers correct; cumulative compounding; cashout.
**Depends on:** S9.

```
Implement engine/src/engine/games/hilo.py per spec_v2 §A.7 as StatefulGame: cards from rng (floor(next()*52)); p(Higher-or-same)=(14-r)/13, p(Lower-or-same)=r/13; step multiplier=(1-edge)/p(side); cumulative product; single debit; cashout. Tie counts as a win for the chosen side. Wire /bet + /action USING the shared S6 one-active-(user,game)-round guard + action serialization (do NOT re-implement it here). Register + endpoints. Tests: per-rank step probs/multipliers; cumulative compounding; full-sequence determinism; parity; single-debit; the shared guard rejects a second concurrent round. Run and paste output.
```
**Verify:** `pytest -q tests/test_hilo_*.py` green (per-rank table matches spec_v2; compounding correct); determinism + parity pass.

---

### S15 — Keno (tuned table)
**Goal:** Keno playable; hypergeometric draw; per-(picks,risk) table tuned to target RTP.
**Depends on:** S9.

```
Implement engine/src/engine/games/keno.py per spec_v2 §A.8: draw 10 distinct of 40 via sampleWithoutReplacement; hits vs picks; payout from a per-(picks,risk) table you TUNE so sum P(h)*pay_h = target after rounding. Validate 1<=|picks|<=10 distinct in-range. Register + endpoint. Tests: hypergeometric hit frequencies; per-(picks,risk) RTP within ±0.2% after rounding; determinism; parity; selection validation. Run and paste output.
```
**Verify:** `pytest -q tests/test_keno_*.py` green; hit-frequency + RTP-within-tol checks pass; invalid selections rejected.

---

### S16 — Roulette (Originals 0–99)
**Goal:** 0–99 colour-pick roulette playable; green 99×, red 2.0204×, black 1.98× at edge 1%.
**Depends on:** S9.

```
Implement engine/src/engine/games/roulette99.py per spec_v2 §A.9: result=floor(rng.next()*100); map to colour via configurable table (1 green / 49 red / 50 black default); settle each placed bet at (1-edge)/p. Support multiple simultaneous bets per spin. Register + endpoint. Tests: result uniform over 0-99; per-bet RTP=0.99 with the configured map; determinism; parity. Run and paste output.
```
**Verify:** `pytest -q tests/test_roulette99_*.py` green; uniformity + per-bet RTP within tol; multi-bet settlement correct.

---

### S17 — Optional Originals extensions (Coinflip, Towers, Wheel)
**Goal:** house-addition Originals playable, clearly labeled as extensions.
**Depends on:** S9.

```
Implement engine/src/engine/games/{coinflip,towers,wheel}.py per spec_v2 §A.11 (Coinflip 1.98x; Towers mult=(1-e)*(T/S)^k survive (S/T)^k; Wheel segments tuned like Plinko). Register + endpoints; label as house extensions. Tests: EV/RTP within tol per game; determinism; parity. This step is OPTIONAL — skip if trimming scope. Run and paste output.
```
**Verify:** `pytest -q tests/test_ext_*.py` green (per-game EV/RTP within tol); or step marked skipped in progress if descoped.

---

# Phase 3 — Crash (P3) — first realtime; the prerequisite for Poker

### S18 — Crash round actor + WS gateway + reveal loop
**Goal:** a single authoritative round actor runs WAITING→LOCKED→RUNNING→CRASHED→SETTLED on a loop, broadcasts ticks over WebSocket via Redis pub/sub, and reveals the committed seed at crash — no betting yet.
**Depends on:** S10.

```
Implement app/src/app/ws/crash.py: one authoritative round actor (one process/partition) computing C at round start from a committed PER-ROUND seed using engine/src/engine/games/_curve.crash_point (reuse S10 — do NOT reimplement). CRASH FAIRNESS (per-round, distinct from the per-user Originals signature): f = randomFloat(roundServerSeed, clientSeed=roundId (the public round salt), nonce=roundNumber, cursor=0); broadcast the per-round commit serverSeedHash during WAITING and reveal serverSeed at crash (the S7 verifier `round` mode reproduces it). Drive the state machine on a timed loop; broadcast {round, tick, crash} events to subscribers via Redis pub/sub so multiple app instances stay consistent. Expose WS /games/originals.crash. The multiplier curve is cosmetic; C is fixed at round start (server is source of truth). No bets/cashouts yet. Test tests/test_crash_loop.py (engine-level): C distribution over 1e6 matches P(C>=x)=(1-edge)/x; instant-bust rate ~ edge. Run and paste output.
```
**Verify:** `pytest -q tests/test_crash_loop.py` green (distribution + instant-bust within tol); a WS client receives round/tick/crash events locally; seed revealed post-crash.

---

### S19 — Crash betting + server-authoritative cash-out
**Goal:** players bet during WAITING and cash out during RUNNING; cash-out multiplier is server-stamped; auto-cashout target `t` wins iff `t ≤ C`.
**Depends on:** S18.

```
Add betting to app/src/app/ws/crash.py: place bet (single debit via ledger) during WAITING with optional autoCashout; manual cashout during RUNNING is decided by the SERVER (stamp authoritative multiplier at receipt; if >=C it's a loss); auto-cashout evaluated server-side against C. Exactly one credit per winning bet; idempotent. IDEMPOTENCY KEYS: each placed Crash bet gets its OWN distinct `betId` (a player may place multiple bets per round), so `idempotencyKey = hash(betId, opType)` stays unique per bet+op. Rules: server-authoritative; single-debit/single-credit; no client-time cashout. Tests tests/test_crash_cashout.py: auto-cashout exactness (t wins iff t<=C); duplicate cashout never double-credits (same betId+opType collapses); two bets by one player in one round settle independently; loss when cashout tick >= C. Run and paste output.
```
**Verify:** `pytest -q tests/test_crash_cashout.py` green (auto-cashout exact; no double-credit; correct loss handling); ledger shows one debit + at most one credit per bet.

---

### S20 — Crash reconnect, recovery, latency-fairness
**Goal:** a client can resume mid-round; a mid-round server restart re-settles deterministically; two simultaneous cash-outs resolve by server receipt.
**Depends on:** S19.

```
Add GET /games/originals.crash/state (resume: current round + the caller's active bets) and recovery: on restart the committed round seed already fixes C, so replay settles deterministically. Add a reconciler for bets stuck non-terminal past TTL. Tests tests/test_crash_recovery.py: simulated mid-round restart re-settles identically; reconnect returns correct active bets; latency-fairness (two cashouts "same tick" both resolve by server timestamp, not client clock). Run and paste output.
```
**Verify:** `pytest -q tests/test_crash_recovery.py` green (deterministic re-settle after restart; reconnect correct; latency-fairness holds).

---

# Phase 4 — Slots framework (P4) — independent of P2/P3

### S21 — Slots framework engine (strips, paytable, evaluator)
**Goal:** a data-driven slot engine computes outcomes from weighted reel strips + paytable + lines/ways; RTP emerges from strip weights, not a clamp.
**Depends on:** S6, S7.

```
Implement engine/src/engine/slots/framework.py: a SlotMachine driven by config (reels x rows, weighted reel strips, symbol set, paytable, paylines or ways-to-win, wild/scatter). play(stake, rng, cfg): pick a stop index per reel from that reel's strip via rng, build the grid, evaluate lines/ways, sum base wins -> total multiplier. No feature modules yet. Rules: engine purity; minor units; outcome from strips (no post-hoc RTP clamp). Tests tests/test_slots_framework.py: a tiny known config yields hand-computed wins; line/ways evaluation correctness; determinism. Run and paste output.
```
**Verify:** `pytest -q tests/test_slots_framework.py` green (known-config wins match hand math; determinism); engine purity grep clean.

---

### S22 — Slot feature modules
**Goal:** free spins, hold-and-spin, and a pick bonus are composable feature modules invoked by the framework.
**Depends on:** S21.

```
Implement engine/src/engine/slots/features.py: free-spins (retrigger-capable), hold-and-spin (lock symbols, respins), pick-bonus (choose-to-reveal). Each is a pure module the framework calls when its trigger fires; all RNG via the passed stream. Rules: engine purity; deterministic given the stream. Tests tests/test_slots_features.py: trigger conditions; feature payouts; determinism. Run and paste output.
```
**Verify:** `pytest -q tests/test_slots_features.py` green (each feature triggers + pays correctly; deterministic).

---

### S23 — Machine #1 config + per-machine RTP CI gate
**Goal:** a complete machine (config + art placeholders) ships, with its RTP simulated in CI within tolerance.
**Depends on:** S22.

```
Add engine/src/engine/slots/machines/machine01.json (full config incl. a feature) + register it; wire POST /games/slots.machine01/spin via the bet loop. Add tests/test_slots_machine01_rtp.py: simulate 1e7 spins ON PR and 1e8 NIGHTLY (RTP COUNTS — slots tier), asserting measured RTP within tolerance (a CLT/binomial confidence half-width for n) of the config target; add the heavy run to the CI rtp-gate job. numpy-vectorized sims allowed in tests/ ONLY. Rule: never tune by clamping outcomes — tune strip weights/paytable; never widen a tolerance to pass. Run and paste output.
```
**Verify:** `pytest -q tests/test_slots_machine01_rtp.py` green (RTP within tol of target over 1e7 on PR / 1e8 nightly); spin endpoint returns grid+wins; CI rtp-gate includes the machine.

---

### S24 — Machines #2–#3
**Goal:** two more machines (different volatility profiles) ship, each behind the same RTP gate.
**Depends on:** S23.

```
Add machine02 (low volatility) and machine03 (high volatility) configs + endpoints + per-machine RTP tests in CI. Demonstrate the "new machine = config + art, not code" claim — no framework code changes beyond config. Run the RTP tests and paste output.
```
**Verify:** `pytest -q tests/test_slots_machine0{2,3}_rtp.py` green (each RTP within tol); diff is config + tests only (no framework code change).

---

# Phase 5 — Table & video poker (P5) — parallelizable (disjoint files)

### S25 — Blackjack
**Goal:** server-authoritative blackjack with configurable rules; rules engine property-tested; basic-strategy EV ≈ published edge.
**Depends on:** S6, S7.

```
Implement engine/src/engine/table/blackjack.py: D-deck shoe drawn from the rng (committed at round start), dealer stands-on-soft-17 (configurable), player actions hit/stand/double/split/insurance/(optional surrender), blackjack pays 3:2, reshuffle at a penetration threshold. StatefulGame: bet/deal -> player actions -> dealer play -> settle. Rules: server-authoritative draws; engine purity; minor units. Tests tests/test_blackjack_rules.py (Hypothesis property tests: bust, blackjack, push, payouts, split/double resolution, dealer draw) + tests/test_blackjack_ev.py (basic-strategy EV sim ~ published edge for the configured rules, ± tol). Run and paste output.
```
**Verify:** `pytest -q tests/test_blackjack_rules.py tests/test_blackjack_ev.py` green (rule invariants hold; EV within tol of published edge for the rule set).

---

### S26 — European Roulette (single-zero wheel)
**Goal:** 37-pocket roulette (game id `table.roulette`) with full bet types; every bet RTP = 36/37.
**Depends on:** S6, S7.

```
Implement engine/src/engine/table/roulette_wheel.py: pocket=floor(rng.next()*37) (0-36); settle all standard inside/outside bets via a payout table (straight 35:1 ... even-money 1:1). Register under game id `table.roulette` (the Originals 0–99 colour-pick game keeps `originals.roulette` — distinct ids by design) + endpoint (multiple bets per spin). Rules: engine purity; minor units. Tests tests/test_roulette_wheel.py: pocket uniform over 0-36; each bet type RTP=36/37 within tol; multi-bet settlement; determinism. Run and paste output.
```
**Verify:** `pytest -q tests/test_roulette_wheel.py` green (uniformity; per-bet RTP=36/37 within tol; settlement correct).

---

### S27 — Baccarat (punto banco)
**Goal:** third-card rules engine exact; edges by enumeration match published values for the configured rules.
**Depends on:** S6, S7.

```
Implement engine/src/engine/table/baccarat.py: Player/Banker/Tie bets; fixed third-card drawing rules (no player decisions); banker 5% commission; tie pays 8:1 (configurable). Shoe drawn from rng. Rules: engine purity. Tests tests/test_baccarat.py: exhaustive third-card-rule correctness; edge by enumeration (Banker ~1.06%, Player ~1.24%, Tie ~14.36% for default rules) within tol; determinism. Run and paste output.
```
**Verify:** `pytest -q tests/test_baccarat.py` green (drawing rules exhaustively correct; enumerated edges match published values for configured rules within tol).

---

### S28 — Video Poker (9/6 Jacks-or-Better)
**Goal:** 5-card draw video poker; paytable-driven; RTP under the specified strategy ≈ published.
**Depends on:** S6, S7.

```
Implement engine/src/engine/table/video_poker.py: deal 5 from a committed deck via rng, hold/draw, evaluate the final hand against a configurable paytable (9/6 JoB default). Reuse a 5-card hand-rank evaluator placed in engine/src/engine/cards/evaluator.py (CARRY-FORWARD: S29 poker will extend this to 7-card best-5 — design it to be shared, do not duplicate). Tests tests/test_video_poker.py: hand-rank correctness; RTP under the specified (e.g. always-optimal-for-fixtures) strategy within tol of published; determinism. Run and paste output.
```
**Verify:** `pytest -q tests/test_video_poker.py` green (hand-rank correct; RTP within tol); `engine/src/engine/cards/evaluator.py` exists and is the shared evaluator.

---

# Phase 6 — Poker PvP (P6) ⚠️ highest risk — depends on the realtime foundation (S18)

### S29 — 7-card hand evaluator
**Goal:** a correct, fast best-5-of-7 evaluator with exhaustive ranking tests; shared with video poker (S28).
**Depends on:** S6. (extends `engine/src/engine/cards/evaluator.py` from S28 if present)

```
Implement/extend engine/src/engine/cards/evaluator.py: best-5-of-7 ranking returning a comparable hand score; handle all categories (high card..straight flush, wheel A-5, etc.). Pure stdlib. Reuse the 5-card core from S28 (do NOT duplicate — CARRY-FORWARD). Tests tests/test_evaluator.py: known hands rank correctly; ordering invariants over many random 7-card sets (each player's best 5 found); ties handled. Run and paste output.
```
**Verify:** `pytest -q tests/test_evaluator.py` green (known hands + ordering invariants + ties); evaluator is shared (no duplicate ranking code; grep).

---

### S30 — Pot / side-pot engine
**Goal:** main + side pots computed correctly under multiple all-ins; chips conserved.
**Depends on:** S29.

```
Implement engine/src/engine/poker/pots.py: given per-player contributions and all-in amounts, compute main + side pots and award them by hand rank at showdown. Pure. The classic bug source — test it hard. Tests tests/test_pots.py (Hypothesis): chips-in == chips-out (conservation) across randomized multi-all-in scenarios; correct eligibility per side pot; split pots on ties. Run and paste output.
```
**Verify:** `pytest -q tests/test_pots.py` green (conservation holds over randomized all-in scenarios; eligibility + splits correct).

---

### S31 — Poker table state machine (engine, deterministic)
**Goal:** a deterministic NLHE table reducer: blinds, button, four betting rounds, action validation, showdown — no networking yet.
**Depends on:** S29, S30.

```
Implement engine/src/engine/poker/table.py as a pure reducer: seats, blinds/button rotation, deal (from rng), betting rounds (preflop/flop/turn/river) with legal-action validation (fold/check/call/raise/all-in, min-raise rules), and showdown using the evaluator + pots engine. No WS/Redis here. Rules: engine purity; determinism. Tests tests/test_poker_table.py: a scripted full hand reaches correct showdown + pot awards; illegal actions rejected; button/blind rotation. Run and paste output.
```
**Verify:** `pytest -q tests/test_poker_table.py` green (scripted hands settle correctly; illegal actions rejected; rotation correct).

---

### S32 — Realtime poker gateway + per-seat redaction
**Goal:** a WS table actor drives the engine reducer, holds state in Redis, and sends each client only its own hole cards (no leakage).
**Depends on:** S31, S18.

```
Implement app/src/app/ws/poker.py: one authoritative actor per table driving engine/src/engine/poker/table.py; state in Redis checkpointed to Postgres; fan-out via pub/sub (reuse the realtime patterns from Crash S18). CRITICAL: per-seat redaction — a client receives only public state + its own hole cards; the server NEVER sends unseen cards. Tests tests/test_poker_ws.py: a multi-client hand plays to showdown over WS; a redaction test asserts no message to seat X ever contains another seat's hole cards. Run and paste output.
```
**Verify:** `pytest -q tests/test_poker_ws.py` green (multi-client hand completes; **no-hole-card-leakage** assertion passes); state survives an actor checkpoint/reload.

---

### S33 — Matchmaking, buy-in, rake
**Goal:** lobby/matchmaking seats players; buy-in debits the ledger; rake is taken and reconciles.
**Depends on:** S32, S3.

```
Implement app/src/app/poker/lobby.py: list/join tables by stakes, seat assignment, buy-in (ledger debit) and leave (ledger credit of remaining stack). Rake: configurable (rake or rake-free); rake is an economy sink booked to the SYSTEM/house account via the ledger (balanced two-row entry). IDEMPOTENCY KEYS (poker): buy-in / leave / rake use `idempotencyKey = hash(tableId, handNo, seat, opType)` so a re-send collapses to the original. Rules: minor units; double-entry; idempotency. Tests tests/test_poker_lobby.py: buy-in/leave move the ledger correctly; a re-sent buy-in/leave/rake (same tableId+handNo+seat+opType) does not double-apply; rake accounting reconciles to the cent over a series of hands. Run and paste output.
```
**Verify:** `pytest -q tests/test_poker_lobby.py` green (buy-in/leave + rake reconcile to the ledger); no balance mutated outside a ledger write.

---

### S34 — Turn timers, disconnect, reconnect
**Goal:** turn timers auto-act (check/fold), disconnects sit-out/auto-muck, reconnect resyncs state.
**Depends on:** S32.

```
Add to app/src/app/ws/poker.py: per-turn timers (auto-check if no bet to call, else auto-fold on timeout), disconnect handling (sit-out / auto-muck), and reconnect/resync (caller resumes with correct public state + own hole cards). Tests tests/test_poker_timeouts.py: timeout auto-acts correctly; disconnect doesn't stall the table; reconnect restores the correct redacted view. Run and paste output.
```
**Verify:** `pytest -q tests/test_poker_timeouts.py` green (timeout/disconnect/reconnect behave correctly; table never stalls).

---

### S35 — Anti-collusion signals
**Goal:** chip-dumping, soft-play, and shared-IP/device patterns emit to a risk log.
**Depends on:** S33, S34.

```
Implement app/src/app/poker/anticollusion.py: detectors for chip-dumping (consistent one-way transfers), soft-play (abnormal fold-to-each-other rates), and shared-IP/device at a table; emit structured risk events to the audit/risk log (no auto-bans). Tests tests/test_anticollusion.py: synthetic collusion fixtures raise the expected signals; clean play raises none (no false positives on the fixtures). Run and paste output.
```
**Verify:** `pytest -q tests/test_anticollusion.py` green (collusion fixtures flagged; clean fixtures clean); risk events written to the log.

---

# Phase 7 — Economy, polish, packaging (P7) — sign-off + deploy LAST

### S36 — Faucets (economy sources)
**Goal:** welcome grant, daily streak, hourly timer, and level-up rewards credit via ledger `grant`.
**Depends on:** S3.

```
Implement app/src/app/economy/faucets.py: welcome grant, daily-streak bonus, hourly timer claim, level-up rewards. This module is the SINGLE OWNER of the welcome grant — idempotent per user with key `hash(userId,'welcome')` — and S8 auth CALLS it (it does not issue its own grant). All credits go through ledger.grant (balanced two-row entry, SYSTEM/house counterparty); timers/cooldowns in Redis. Rules: double-entry; idempotency; minor units. Tests tests/test_faucets.py: each faucet grants exactly once per eligibility window and traces to a grant LedgerEntry; the welcome grant is idempotent under re-trigger (single grant for `hash(userId,'welcome')`). Run and paste output.
```
**Verify:** `pytest -q tests/test_faucets.py` green (each faucet grants once per window; all via ledger grants).

---

### S37 — Leaderboards + sinks
**Goal:** Redis sorted-set leaderboards (e.g. biggest win, wagered) and the economy sinks are wired/tuned.
**Depends on:** S3.

```
Implement app/src/app/economy/leaderboards.py using Redis sorted sets (biggest-win, total-wagered, profit) with periodic durable snapshots. Confirm sinks (house edge per game, poker rake) flow to the house account in the ledger. Tests tests/test_leaderboards.py: scores update on settle; ranking queries correct; a snapshot persists. Run and paste output.
```
**Verify:** `pytest -q tests/test_leaderboards.py` green (scores update + rank correctly; snapshot persists).

---

### S38 — Responsible-gaming stubs
**Goal:** session-time reminder, self-imposed spend/loss limits, self-exclusion, and a persistent "play money — not real" badge are enforced at the pre-bet gate.
**Depends on:** S6.

```
Implement app/src/app/rg/: can_bet() now enforces self-imposed spend/loss/session limits, cool-off, and self-exclusion (typed block reasons surfaced to the UI), and emits session.elapsed reality-check events. Expose a "not real money / no prizes" flag for the client to render persistently. Rules: a blocked debit fails with a typed RG reason, not a generic error. Tests tests/test_rg.py: a user over a self-set limit / self-excluded is blocked with the correct reason; reality-check event emitted. Run and paste output.
```
**Verify:** `pytest -q tests/test_rg.py` green (limits + self-exclusion block with typed reasons; reality-check emitted).

---

### S39 — Observability
**Goal:** structured logging, metrics, and per-game RTP monitoring (drift alerts) are in place.
**Depends on:** S6.

```
Add structured logging (trace/bet IDs), Prometheus-style metrics (bets, payouts, latency), and a per-game realtime RTP monitor that flags drift from configured target. Rules: no secrets in logs. Tests tests/test_observability.py: metrics increment on a settled bet; the RTP monitor flags a synthetic drift. Run and paste output.
```
**Verify:** `pytest -q tests/test_observability.py` green (metrics increment; drift monitor fires on synthetic drift); logs are structured.

---

### S40 — Load test (HUMAN SIGN-OFF — no code cut) ⛔ DEFERRED/GATED
**Goal:** prove realtime integrity + ledger correctness under load before deploying.
**Depends on:** S20 (always); S35 only if poker was built. (If poker (P6) was cut, S40 gates on S20 alone — the Crash scenario suffices.)

```
Bring the stack up (docker compose up) and run load scenarios: one Crash round with N concurrent clients and — IF poker was built — one busy poker table under timer churn (skip the poker scenario when P6 was descoped). Capture: no double-credits, ledger reconciles to the cent post-run (per-wallet balance == Σ entries AND system-wide Σ(all entries) == 0), p95 latency within target, no deadlocks. Record findings + a written go/no-go in 2026-06-27_casino-games_plan.md. This is VALIDATION + sign-off, not a code change. Requires live infra — runs with human authorization, not in an unattended loop.
```
**Verify:** load run completed; ledger reconciles to the cent; no double-credit; p95 within target; **written go/no-go recorded.** **Do not proceed to deploy without sign-off.**

---

### S41 — Deploy play-money demo (PAID / LIVE — explicit "go") ⛔ GATED
**Goal:** a public play-money demo is live.
**Depends on:** S40.

```
Deploy the API container + managed Postgres + Redis to Fly.io/Render/Railway; deploy the verifier (and, IF a client was built, the static client) to Pages — the client is out of the S1–S42 critical path, so omit it cleanly if none exists. Configure env/secrets via the platform (never in source). This is a PAID/LIVE operation — run only on the user's explicit go; the user authorizes/performs the account + billing actions. Verify the live health check, a real play-money bet end-to-end, and the public verifier.
```
**Verify:** live `/health` 200; an end-to-end play-money bet succeeds on the deployed instance; the public verifier reproduces it (and, if a client was built, the static client loads). (Human-authorized; not run in an unattended loop.)

---

### S42 — Portfolio packaging
**Goal:** the repo reads as a 5-minute-skimmable portfolio piece.
**Depends on:** S41.

```
Write README.md ("how provable fairness works" + live verifier link; architecture diagram; RTP-gate screenshot; run-locally — and, IF a client was built, a 60-second demo GIF) and a docs/ index. Map each headline artifact (verifier, RTP CI gate, double-entry ledger, server-authoritative Crash, poker side pots/anti-collusion) to an iGaming competency. Include the spec_v2 §5 "real-money extension" appendix. The four kit docs already live in the repo-root `docs/` — keep them there (do NOT relocate into a nested project dir). Run a link check and paste output.
```
**Verify:** README renders with verifier link + RTP-gate screenshot (plus the demo GIF if a client was built); `docs/` contains the four kit docs; link check passes; a fresh reader can run locally from the README alone.

---

## Notes on "standalone & verifiable"
- **P1–P6 ship dark / play-money only** — every step is additive and safe to land before anything user-facing depends on it. There is no live path to gate until P7.
- **Each game step is independently verifiable** by its own RTP/EV + determinism (+ parity) tests — the property the whole worker→reviewer loop relies on.
- **Only S41 (deploy) is "destructive"/paid**, gated on S40's sign-off; until then rollback is just not deploying. **S40 depends on S20 always**, and on S35 **only if poker (P6) was built** — descope poker and S40 gates on S20 alone (Crash scenario only).
- **Two CARRY-FORWARDs to honor:** `engine/src/engine/games/_curve.py` (S10) is reused by Crash (S18 — a hard DAG edge); the card evaluator `engine/src/engine/cards/evaluator.py` is shared between video poker (S28, 5-card core) and poker (S29, best-5-of-7), extended not duplicated.
- **Evaluator serialization (S28 ↔ S29):** both touch `engine/src/engine/cards/evaluator.py`, so if both are in scope, SERIALIZE them (shared file) — whichever runs first CREATES the evaluator; the other EXTENDS it. Never run them concurrently and never duplicate the ranking.
- If a step's Verify fails, **stop and fix in that step** — never stack the next change on red.
