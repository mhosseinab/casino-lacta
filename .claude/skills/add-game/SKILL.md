---
name: add-game
description: >-
  The canonical recipe for adding a new game (Original, slot machine, or table game) to the
  casino — pure engine outcome fn → register → wire the shared bet loop → RTP/EV + determinism +
  (Originals) verifier-parity tests, honoring every iron rule. Use when implementing any of steps
  S9–S17, S21–S28, or adding a game beyond S42.
---

# Adding a game

Every game ships the same vertical slice (build-plan §5 Definition of Done). A new game supplies
ONLY its pure outcome function + input/limit schema + tests; the wallet, ledger, fairness, and bet
loop already exist. Read `CLAUDE.md`, the game's step in
`docs/2026-06-27_casino-games_implementation-steps.md`, and its math in `docs/..._spec_v2.md`
(Appendix A) before writing code. For FastAPI / SQLAlchemy 2.0 async / Pydantic v2 / pytest-asyncio
API specifics, consult **Context7** (`resolve-library-id` → `query-docs`) — don't guess framework
syntax. (Engine outcome logic itself is stdlib-only and needs no framework.)

## TDD first (non-negotiable)

**RED → GREEN → REFACTOR.** Write the test before the production code, every time:
1. **RED** — write the RTP/EV (or determinism / parity / rules / single-debit) test against the
   not-yet-written engine fn. Run it; confirm it FAILS for the right reason (fn missing/wrong, not a
   typo). A test that passes on first write proves nothing — delete and rethink it.
2. **GREEN** — write the simplest engine fn + wiring that makes it pass. No extra features.
3. **REFACTOR** — clean up with the tests green.
The step's **Verify block is the test contract** — run it and paste ACTUAL output. Never weaken,
skip, or vacuously assert a test to go green; escalate instead. (Throwaway table/math exploration is
exempt — but the moment you keep a result, write its test first.)

## Recipe

1. **Pure engine fn** — `engine/src/engine/games/<name>.py` (slots: a config under `engine/src/engine/slots/machines/`;
   table: `engine/src/engine/table/<name>.py`). Conform to the `engine/src/engine/types.py` protocol:
   - **Instant** (Dice, Limbo, Pocket Dice, Plinko, Keno, Roulette, Video Poker):
     `play(input, rng, cfg) -> Outcome`.
   - **Stateful** (Mines, HiLo, Blackjack, Baccarat): `init(input, rng, cfg) -> state` commits the
     layout/shoe from `rng` AT ROUND START; `step(state, action) -> (state, Outcome | None)`.
   - **Purity:** stdlib only (`hashlib`, `hmac`, `math`, `dataclasses`, `typing`). NO
     `random`/`secrets`/IO/clock/framework. All entropy via the passed `RngStream`.
   - **Money:** outcome carries a `multiplier` (float); amounts stay integer minor units — let
     `engine/money.apply_multiplier` (which rounds DOWN — floored, the truncated fraction is house
     margin) + `cap(payout, maxWin)` (applied AFTER rounding) do the arithmetic in the bet loop.
     The RTP test must pick `(stake, maxWin, maxMultiplier)` so the cap does NOT bind (measure the
     uncapped math); test the cap separately (`payout == maxWin` when exceeded).
   - **Reuse CARRY-FORWARDs:** Crash/Limbo share `engine/src/engine/games/_curve.py`; poker/video-poker share
     `engine/src/engine/cards/evaluator.py`. Never duplicate them.

2. **Register** in `engine/src/engine/registry.py`: `id -> (module, default GameConfig)`. Ids are namespaced
   (`originals.dice`, `slots.machine01`, `table.blackjack`). The registry entry is only the
   defaults/seed: at runtime the authoritative limits/config come from the seeded DB
   `GameConfig`/`GameLimit` rows (`seed_configs()`), and `GameConfig.version` is stamped into the
   `AuditEvent` as `configVersion` — never hardcode limits in the game.

3. **Wire** the shared bet loop — no per-game ledger code. `POST /games/{id}/bet` (single debit →
   `play`/`init` → `apply_multiplier` → single credit on win → `AuditEvent` linking
   seed+nonce+input+configVersion). Stateful games also use `/games/{id}/action` (reveal, cashout,
   hit/stand…) and `GET /games/{id}/state`. The one-active-round-per-`(user, game)` constraint and
   serialized actions are owned by the shared S6 bet-loop stateful path — your game USES that guard,
   it does NOT re-implement it.

4. **Tests** (this is the deliverable as much as the code — see the `add-game` companion skill
   `rtp-harness` for the gate mechanics):
   - **RTP/EV** — `tests/test_<name>_rtp.py` via `tests/rtp_harness.py#run_rtp`, ≥1e6 trials
     (1e7 for headline Originals), measured RTP within the step's tolerance of `1 - edge`. For
     tuned tables (Plinko/Keno/slots) tune weights/paytable to target — **never clamp outcomes**.
   - **Determinism** — same `(serverSeed, clientSeed, nonce, input)` → identical outcome.
   - **Verifier parity** (Originals) — `verifier/` reproduces a sample bet from its seeds.
   - **Stateful invariants** — single-debit; no reveal/action after a terminal state.
   - **Rules** (table games) — Hypothesis property tests for bust/blackjack/push/split, drawing
     rules, edges matching published values.

5. **Gate it in CI** — add the RTP test to the `rtp-gate` job. A game is not done until its RTP
   test runs green in CI.

6. **Per commit:** `ruff check .` + `mypy engine app verifier` clean; engine-purity grep clean; stage files
   by name; Conventional Commits (`feat(dice): …`); update the doc if behavior changed.

## Done when
Every Definition-of-Done box (build-plan §5) is checked: pure engine fn, RTP/EV test in CI,
determinism test, wired single-debit/single-credit bet loop, verifier parity (Originals), max-win
cap + bet limits + RG gate honored, `AuditEvent` written. **Do not weaken a tolerance to pass —
escalate instead.**
