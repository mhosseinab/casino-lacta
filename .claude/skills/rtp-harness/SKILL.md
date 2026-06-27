---
name: rtp-harness
description: >-
  How to author, run, and tune the RTP/EV-as-CI gate — the project's signature artifact — for any
  game, including tuning Plinko/Keno/slot multiplier tables to a target RTP without clamping
  outcomes. Use when writing tests/rtp_harness.py, a per-game *_rtp.py test, the CI rtp-gate job,
  or tuning a published multiplier table (steps S7, S13, S15, S17, S23–S24, and every game's RTP test).
---

# RTP-as-CI gate

The RTP gate is the headline portfolio artifact: CI fails if any game's measured Return-To-Player
drifts from its configured target, proving the math automatically on every commit. Treat a failing
RTP gate as a real defect, never as "flaky" to be loosened.

## TDD: the RTP test is written first (RED)

Author the `*_rtp.py` gate BEFORE the engine fn / before tuning a table. Run it against the
not-yet-correct code and confirm it FAILS (RTP out of tolerance) — that proves the gate actually
binds. Then implement / tune to GREEN. A gate that passes before any tuning is vacuous. Never widen
a tolerance to reach green — fix the math or the table, or escalate.

## The shared harness

`tests/rtp_harness.py` exposes `run_rtp(game, input, n, target, tol)`:
- Runs `n` trials over a **seeded** `RngStream` (vary `nonce`/`client_seed` per trial; the run is
  reproducible — record the base seed so a failure is debuggable).
- Accumulates `sum(payout_minor) / sum(stake_minor)` as measured RTP.
- Asserts `abs(measured - target) <= tol`.
- For **stateful** games, drive a fixed policy (e.g. always-reveal-then-cashout-at-k for Mines,
  basic strategy for Blackjack) so EV is well-defined; document the policy in the test.

### Trial counts & tolerances — tiered (PR subset vs. nightly), do not relax

The gate runs in two tiers. The PR / `pytest` tier is a FAST subset; the heavy sims run in the
nightly/manual `rtp-gate` job. The tolerance is a **statistical confidence half-width** (CLT for a
mean RTP, binomial for a frequency), computed from the trial count — NOT a flat ±0.5% pulled from
the air. More trials ⇒ a tighter, more demanding tolerance.

- **PR subset:** ~1e6 trials, tolerance = the CLT/binomial confidence-interval half-width at that
  `n` (record the base seed so a failure reproduces).
- **Nightly / manual `rtp-gate`:** the heavy 1e7–1e8 sims (slots: **1e7 on PR, 1e8 nightly**),
  tolerance again the CI half-width at the larger `n`.
- **Crash/Limbo:** the tail distribution `P(C≥x)=(1-edge)/x` within the binomial CI over the run.

**numpy-vectorized sims are allowed in `tests/` ONLY** (never in `engine/` — purity), to make the
heavy counts tractable; the engine outcome fn under test stays pure-Python and seeded.

**MaxWin must NOT bind the gate.** Pick `(stake, maxWin, maxMultiplier)` so the top reachable
payout (`maxMultiplier × stake ≤ maxWin`) never trips the cap — the gate measures the *uncapped*
math. Cap behavior gets its OWN unit test (`payout == maxWin` when the raw payout exceeds it),
separate from the RTP run.

**Rounding biases RTP downward.** `engine.money.apply_multiplier` floors, so realized RTP ≤ target;
choose stakes large enough that the per-bet rounding bias is ≪ the tolerance (otherwise the floor
alone can push a correct table out of band).

## Tuning a multiplier table (Plinko / Keno / slots / Wheel)

The outcome distribution is fixed by the math (Binomial bins for Plinko, hypergeometric hits for
Keno, weighted reel strips for slots). You tune the **payouts/weights** so expected value hits
target — you NEVER clamp or reject an outcome to force RTP.

1. Compute the exact outcome probabilities `P(i)` analytically (Binomial / hypergeometric / strip
   weights) — assert empirical frequencies match `P(i)` in a separate test.
2. Solve the published multipliers `m_i` so `sum(P(i) * m_i) == target` **after** rounding `m_i` to
   the displayed precision (2 dp). Rounding shifts RTP — re-measure post-rounding and nudge a few
   entries until within tolerance.
3. For slots, tune **strip weights and paytable**, not a post-hoc multiplier — RTP must emerge from
   the configured machine, with feature-trigger rates also correct.
4. Keep the rendered path consistent with the outcome (Plinko's animated ball ends in the server
   bin; the curve is cosmetic — `C` is fixed at round start).

## CI

Add each game's `tests/test_<name>_rtp.py` to the dedicated **rtp-gate** job in
`.github/workflows/ci.yml` (separate from the unit `pytest -q` so the heavy sims are visible). A
game is not "done" until its RTP test is green in that job. If a gate fails, fix the math or the
table — **escalate before ever widening a tolerance.**
