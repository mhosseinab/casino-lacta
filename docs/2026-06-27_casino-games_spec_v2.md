---
type: spec
project: casino-games
tags: [casino, igaming, games, originals, slots, table-games, poker, provably-fair, rng, fastapi, social-casino, play-money, spec, dev]
status: draft; gaps-patched 2026-06-27
version: v2 (merged)
audience: ai-build-agent + portfolio reviewer
money_model: social / play-money, NON-REDEEMABLE (real-money is a documented extension, §7)
stack: Python / FastAPI (async) · Postgres · Redis · WebSockets · thin web client
build_target: portfolio iGaming showcase (production-shaped, not production-licensed)
scope: full games layer — provably-fair Originals + in-house slots/table/poker + optional 3rd-party integration
default_house_edge: 0.01 (Originals, configurable 0.00–0.05); slots/table per-game RTP 0.92–0.99
date: 2026-06-27
supersedes_drafts: [2026-06-27_casino-games_spec.md, 2026-06-27_casino-games_build-plan.md, "2026-06-27_casino-games_build-plan copy.md"]
math_verified: 43/43 checks re-run in-session (Appendix A)
---

# Online Casino — Games Build Specification (v2, merged)

> **Purpose.** A complete, buildable specification of the **games layer** of an online casino, written to be handed to an AI build agent *and* to read as an iGaming engineering portfolio piece. It covers (A) in-house provably-fair **Originals**, (B) in-house **slots, table games, and video poker**, (C) **PvP poker**, and (D) the optional **integration layer** for third-party content. Every Originals math model was re-verified by simulation/enumeration this session (Appendix A).

> **Money model (decided).** This is a **social / play-money** product. Virtual credits only — **non-redeemable**, no cash-out, no prizes, no sweepstakes. That keeps the legal surface small (§7) while still demanding real-engine rigor: server-authoritative outcomes, double-entry ledger, provable fairness, audit. **Real-money is architected for but not enabled** — the seams are documented in §7 so the same engine could be promoted to a licensed real-money product.

> **Why this is a strong portfolio piece.** Provable fairness (commit–reveal, an open verifier), a double-entry coin ledger that reconciles to the cent, server-authoritative state machines, and an RTP-as-CI-gate are the *hard, differentiated* iGaming skills. A blackjack clone is not. This spec leads with the parts that demonstrate them.

> **Reading order.** §0 → §1 → §2 in full before writing code. §2 (platform spine) defines the contracts every game depends on. Do not implement any game in Parts A–C without first implementing the §2.2 Wallet/Bet contract and §2.3 RNG/Fairness service.

---

## 0. How to use this document

**0.1 This is a merge of three prior drafts.** It reconciles, in one authoritative spec:
- the **Gamdom-style Originals** spec (provably-fair Dice/Crash/Mines/…) — kept in full, math re-verified;
- the **social-casino product** plan (slots/table/poker, economy, compliance) — folded into Parts B/C and §7;
- the **client-side React demo** plan — superseded; its "pure engine, server-authoritative shape" discipline is preserved, but the canonical target here is a **FastAPI server**, not a browser-only demo (a browser-only build cannot be server-authoritative — see §2.7).

**0.2 Scope boundary.** Fully specified: every game's rules, math, RTP, RNG mapping, state machine, API, limits, edge cases, tests; and the **interfaces** each game needs (wallet, fairness, limits, responsible-gaming, audit). Out of scope (referenced as external interfaces): account system internals, payment/IAP rails, CMS, affiliate, UA/analytics pipeline.

**0.3 Conventions.**
- `MUST` / `SHOULD` / `MAY` per RFC 2119.
- Money is integer **minor units** (cents / coin-cents) — never floats (§2.1).
- Multipliers/odds are decimal (`1.98` = 1.98×). `edge` = house edge fraction; `RTP = 1 − edge`.
- Code samples are illustrative (Python/TypeScript pseudo), not final.
- ✅ **VERIFIED** marks math re-checked in Appendix A. ⚠️ **VERIFY** marks a value carried from an external source (e.g. a real operator's public pages, legal/benchmark claims) that must be re-checked before relying on it.

**0.4 Decision log (baked into this spec).**

| Decision | Choice | Consequence |
|---|---|---|
| Money model | **Social / play-money, non-redeemable** | No KYC/payment/cash-out internals; light legal regime (§7); economy uses RTP as a pacing knob |
| Backend stack | **Python / FastAPI (async)** | One language for engine + math harness + verifier; WebSockets for realtime; Go noted as the production hot-path option (§2.11) |
| Games | **Originals + in-house slots/table/video-poker + PvP poker** (+ optional 3rd-party) | Superset; Parts A/B/C built, Part D optional |
| Fairness | **Provably fair for Originals; server-authoritative + CSPRNG + audit for the rest** (provably-fair shuffle optional for poker/table) | §2.3 |
| House edge | **Configurable; Originals default 1%; slots/table 0.92–0.99 per game** | RTP is the economy's primary sink |
| Real money | **Designed-for, not enabled** | Extension seams documented (§7); requires licence + RNG cert + KYC/AML/geo before flipping on |

**0.5 What "portfolio showcase" means here.** Production-*shaped* (idempotency, audit, tests, reconciliation) but not production-*licensed*. The smallest impressive slice (§8) is the platform spine + Dice + Mines + Crash in play-money, provably fair, with a working verifier and an RTP CI gate. That demonstrates the whole engine end-to-end.

---

## 1. Product overview

**1.1 What it is.** A web-first, **server-authoritative social casino**. Players receive non-redeemable virtual credits on a cadence and wager them across four game families. The browser only renders; the server decides every outcome and moves every credit.

**1.2 Game taxonomy.**
```
Casino
├── Originals      (Part A — build)   provably fair, instant, single-player (Crash is multiplayer)
├── Slots          (Part B — build)   data-driven reel framework; new machine = config + art
├── Table Games    (Part B — build)   Blackjack, European Roulette (wheel), Baccarat, Video Poker
├── Poker          (Part C — build)   PvP Texas Hold'em; realtime, matchmaking, anti-collusion
└── [Integrated]   (Part D — option)  3rd-party slots/live via aggregator + seamless wallet
```

**1.3 Non-negotiable cross-game properties.**
1. **Server-authoritative** outcomes. Client sends *intent*; server decides and returns the result. The single most important anti-cheat invariant.
2. **Atomic, idempotent** credit movement (§2.2). No double-debit, no double-credit, ever.
3. **Double-entry ledger** is the single source of truth for balances (§2.2). Wallet balance is a reconciled projection, never edited directly.
4. **Provably fair** for all Originals (§2.3); server-authoritative + audited CSPRNG for the rest.
5. **Configurable RTP/edge** per game (§2.5); economy depends on it.
6. **Full audit trail** — every outcome reconstructable from `(seed, nonce, input, config-version)` (§2.10).

**1.4 Non-goals (v1).** Real-money wagering, cash/prize redemption, sweepstakes mechanics, crypto/on-chain, native-first development, true live human dealers (a "simulated live" host is the cheaper, safer option if the *feel* is wanted).

---

## 2. Platform spine (build this first)

### 2.1 Currency, minor units, and play-money mode
- **Minor units only.** Store/compute all amounts as 64-bit integer minor units (coin-cents). Reject floats at the API boundary. Formatting is presentation-only.
- **Rounding rule (MUST).** `apply_multiplier(stakeMinor:int, multiplier:float) -> int` rounds **DOWN (floor)** to the nearest minor unit; the truncated fraction is house margin. The payout `cap(payout, maxWin)` (§2.5) applies **after** rounding. This is the single rounding owner — no float is ever stored or settled.
- **Currency abstraction.** A bet references a `walletId` + `currency`. Game math is currency-agnostic — it computes a **multiplier**; the wallet applies it to the stake. MVP currency: one play-money `GOLD` (optionally a non-purchasable second currency `GEMS` for cosmetics/LiveOps).
- **Mode flag (extension seam).** `mode ∈ {PLAY, REAL}` derives from the wallet, not the game. In this product only `PLAY` is enabled. `REAL` activates the compliance gates in §7 and is intentionally left unwired. Every game runs identically in both modes; only the wallet behind it differs.
- **Per-currency limits.** Min-bet, max-bet, max-win caps are keyed by `(gameId, currency)` (§2.5).

### 2.2 Wallet / Bet contract + double-entry ledger (the single most important interface)
Every game is a state machine that moves credits **only** through this contract. The wallet/ledger is a first-class service you build (it is the trust core).

**Ledger rule (MUST).** Credits move **only** by writing an append-only, double-entry `ledger_entry`. Each movement debits one account and credits another by equal magnitude; the sum of entries for a wallet equals its balance. The `wallet.balance` column is a cached projection reconciled against the ledger by a scheduled job. Never mutate a balance directly.

**House counterparty (MUST).** Every player movement has a real counterparty: a reserved **SYSTEM (house) account** — `User(id="SYSTEM")` plus one house `Wallet` per currency (`userId=SYSTEM`). Every `debit`/`credit`/`grant`/`rollback`/rake writes a **balanced two-row `LedgerEntry` set sharing one `txnId`** — the player row and the house row sum to zero. There is no "money from nowhere"; the faucet `grant` is the house funding a player, not an unmatched credit.

**Invariants (MUST):**
- Per-wallet: `reconcile(walletId)` ⇒ `balance == Σ entries` (to the cent, under concurrency).
- System-wide (true double-entry): `Σ(all entries) == 0` across every wallet including SYSTEM.

**Operations (all idempotent via `idempotencyKey`):**

| Op | Direction | When | Notes |
|---|---|---|---|
| `debit` (place bet) | player → house | round/bet starts | Takes stake. Fails on insufficient funds, limit breach, RG block. |
| `credit` (settle win) | house → player | round settles as a win | `stake × multiplier` (multiplier 0 = loss → no credit). |
| `rollback` | reverse a prior op | error/timeout/cancel | Idempotent reversal of a specific `debit`/`credit`. |
| `grant` (faucet) | house → player | bonus/daily/level-up | Economy source (§7 build-plan). |

**Contract rules (MUST):**
1. **Idempotency.** Every op carries `idempotencyKey = hash(betId, opType)`. Re-sends return the original result, never re-apply. **Crash** issues a **distinct `betId` per placed bet** (a round holds many bets), so each bet's keys stay unique; **poker** buy-in/leave/rake key on `hash(tableId, handNo, seat, opType)` instead of a single `betId`.
2. **Single-debit, single-credit per bet.** Multi-step games (Mines/Crash/HiLo) debit **once** at start; the running multiplier is internal until cash-out, which triggers exactly one `credit`.
3. **Settle is atomic.** Outcome determination + ledger write are one DB transaction (or a saga with guaranteed compensation). A crash between "decided" and "credited" self-heals via the bet's terminal state + reconciliation (§2.10).
4. **No negative balance.** `debit` is the only gate; if it succeeds, the round may proceed.

**Reference bet object:**
```json
{
  "betId": "01J…ULID",
  "gameId": "originals.dice",
  "userId": "…", "walletId": "…",
  "currency": "GOLD", "mode": "PLAY",
  "stakeMinor": 100,
  "status": "PLACED|ACTIVE|WON|LOST|CASHED_OUT|VOIDED|ERROR",
  "fairness": { "serverSeedHash": "…", "clientSeed": "…", "nonce": 42 },
  "input": { "...game-specific..." },
  "outcome": { "...game-specific...", "multiplier": 1.98, "payoutMinor": 198 },
  "createdAt": "…", "settledAt": "…",
  "idempotencyKeys": { "debit": "…", "credit": "…" }
}
```

### 2.3 RNG & Provably-Fair service
**(a) Cryptographic RNG.** All outcomes derive from a CSPRNG (`secrets` / `os.urandom` in Python — **never** `random`/`Math.random` for outcomes).

**(b) Provably-fair scheme (Originals — MUST).** Standard **server-seed / client-seed / nonce** commit–reveal:
```
serverSeed       : 256-bit secret, generated per (user, rotation)
serverSeedHash   : SHA256(serverSeed) — shown to player BEFORE betting (commitment)
clientSeed       : player-chosen string (editable)
nonce            : per-(user,serverSeed) monotonic counter, +1 each bet
randomFloat(cur) : bytes = HMAC_SHA256(serverSeed, f"{clientSeed}:{nonce}:{cursor}")
                   take 4 bytes → uint32 → /2^32 → uniform [0,1); bump cursor for more
```
- **Commit–reveal.** Player sees `serverSeedHash` before betting. On **seed rotation** the old `serverSeed` is revealed so any past outcome can be recomputed from `(serverSeed, clientSeed, nonce)`.
- **Verifier.** Ship an open verifier (web page + reference code) per Original. The reference code MUST be the **same** derivation the server runs (Python module reused) so parity is guaranteed.
- **Determinism rule (MUST).** Given `(serverSeed, clientSeed, nonce, gameInput)` the outcome is a pure function. No wall-clock, no DB state, no `Math.random` in the derivation.
- **Seed rotation** MUST be user-initiable any time and forced on a schedule; never reuse `(serverSeed, nonce)`.

**(c) Slots / table / video-poker.** Server-authoritative CSPRNG + per-round audit record (seed/draw, config version) is **required**. Extending the commit–reveal scheme to these (e.g. a committed shoe shuffle for blackjack/baccarat, committed reel stops for slots) is **recommended** and cheap given (b) — a strong "everything here is verifiable" portfolio line.

**(d) Poker.** Server-authoritative shuffle from CSPRNG; never leak unseen hole cards to any client. Optional: commit a hash of the shuffled deck pre-deal, reveal post-hand (provably-fair shuffle). Integrity emphasis is anti-collusion (§2.7, Part C).

### 2.4 Outcome derivation helpers (used across Part A)
```
floatToInt(f, n)        = floor(f * n)                                  # uniform int in [0,n)
shuffle(deck, rngStream)= Fisher–Yates using consecutive randomFloats   # cards
sampleWithoutRepl(N,k)  = draw k distinct from N                        # Keno, Mines, Roulette
crashPoint(f, edge)     = max(1.00, floor((1-edge)/(1-f) * 100)/100)    # Crash & Limbo
```
Each Part A game states which helper it uses and how many draws it consumes, so the verifier is exact.

**Crash `f` derivation (MUST — per-round, not per-user).** Limbo derives `f` from the per-user `(serverSeed, clientSeed, nonce)`; **Crash** derives `f` from a **per-round seed**: `f = randomFloat(roundServerSeed, clientSeed=roundId, nonce=roundNumber, cursor=0)`, where `roundId` is a public round salt. The open verifier therefore needs a distinct **`round` mode** alongside the per-user Originals signature (see §A.4, §2.3b).

### 2.5 Bet limits, max win, validation (per game, per currency)
Config table `game_limits(gameId, currency) → { minBetMinor, maxBetMinor, maxWinMinor, maxMultiplier }`. Server MUST validate **before** `debit`:
1. stake within `[minBet, maxBet]`;
2. `stake × maxPossibleMultiplier ≤ maxWin` (else **cap payout** at `maxWin` (default) or reject — per game);
3. game-specific input validity;
4. RG limits (§2.6) pass.

**Config authority (MUST).** At runtime the DB rows `GameConfig`/`GameLimit` (§2.9) are **authoritative**; the engine `registry.py` supplies only **defaults/seed values**. Rows are **seeded** by a `seed_configs()` data migration from those defaults, then hot-updatable. `configVersion` (= `GameConfig.version`) is stamped into every `AuditEvent` so an outcome is reconstructable against the exact config that produced it.

**MaxWin vs RTP (note).** Because rounding is floor and the cap applies **after** rounding (§2.1), capping payout at `maxWin` truncates the tail and **lowers** measured RTP. RTP sims therefore pick `(stake, maxWin, maxMultiplier)` so the cap does **not** bind — the gate measures the uncapped math; cap behaviour is unit-tested separately (never folded into the RTP gate).

### 2.6 Responsible-gaming hooks (built even though social)
RG is lighter for non-redeemable play-money, but the hooks are first-class (they're part of the showcase and the real-money seam):
- **Pre-bet gate:** `can_bet(userId, stakeMinor)` checks session-time limit, self-imposed spend/loss limit, cool-off, self-exclusion. A blocked `debit` MUST fail with a typed reason; UI shows the RG message.
- **Reality checks:** emit `session.elapsed` so the platform can interrupt long sessions.
- **Bounded autobet:** every auto/turbo mode MUST have a max-rounds and stop-on-loss-limit.
- **Honest framing:** persistent "play money — not real, no prizes" badge (§7).

### 2.7 Anti-fraud / anti-cheat
- **Server-authoritative outcomes** (restated): client sends intent (place / reveal tile / cash out); server decides. Never trust client-sent outcomes or multipliers. *This is why a browser-only build is a demo, not the product — the prior React-only draft is superseded for this reason.*
- **Replay/idempotency** on every state-changing call (§2.2).
- **Rate & velocity limits** per user/IP/game; bound autobet throughput.
- **Bounded concurrency:** one active round per `(user, game)` for stateful games (Mines/HiLo/Crash-bet) unless the game explicitly supports parallel bets.
- **Seed integrity:** never expose `serverSeed` before rotation; detect nonce gaps.
- **Anomaly signals** to a risk log: improbable win streaks, latency-arbitrage on Crash cash-out, collusion patterns in poker (§Part C).

### 2.8 State, concurrency, idempotency model
- Each stateful round is a **persisted state machine** with a single owner; transitions are guarded and logged.
- Serialize a round's transitions via optimistic locking (version column) or a per-round actor/queue.
- **Crash recovery:** terminal outcome is derivable from persisted `(seed, nonce, input)`; a reconciler re-settles any bet stuck in a non-terminal state past its TTL.

### 2.9 Core data model (games layer)
```
User(id, …external…)                                       # includes a reserved User(id="SYSTEM") — the house
Wallet(id, userId, currency, mode, balanceMinor)            # balance is a reconciled projection
  # House account: Wallet(userId=SYSTEM, currency, …) — one per currency; counterparty of every player movement (§2.2)
LedgerEntry(id, walletId, txnId, type[WAGER|WIN|GRANT|ROLLBACK|PURCHASE], deltaMinor, balanceAfter, ref, ts)   # append-only, double-entry; rows sharing a txnId sum to zero
  # PURCHASE = seam; unused in PLAY mode (IAP is out of scope, §0.2)
GameRound(id, gameId, type[SINGLE|MULTIPLAYER], serverSeedId, nonce, status, input, outcome, configVersion, createdAt, settledAt)
Bet(id, roundId, userId, walletId, currency, mode, stakeMinor, status, multiplier, payoutMinor, idem…)
ServerSeed(id, userId, seedHash, seedEncrypted, rotatedAt|null)    # revealed only after rotation
ClientSeed(userId, value, updatedAt)
NonceCounter(userId, serverSeedId, value)
GameConfig(gameId, version, edge, params, paytable, rtp, volatility, enabled)   # versioned, hot-updatable, seeded from engine defaults (§2.5); GameRound.configVersion = GameConfig.version, stamped into AuditEvent
GameLimit(gameId, currency, minBet, maxBet, maxWin, maxMultiplier)
PokerTable(id, stakes, seats[], state, handNo)              # Redis during play, checkpointed
Jackpot(id, gameId, poolMinor, popParams)                  # optional (A.10)
AuditEvent(id, betId|roundId, type, payload, ts)           # immutable
```

### 2.10 API surface & audit
- **Transport:** REST/JSON for instant games; **WebSocket** for realtime multiplayer (Crash, Poker) and streamed round/multiplier updates.
- **Common envelope:**
```
POST /games/{gameId}/bet      → places debit, returns roundId (+ outcome if instant)
POST /games/{gameId}/action   → reveal/step/cashout for stateful games
GET  /games/{gameId}/state    → current round (resume after reconnect)
GET  /fairness/{betId}        → seeds (+revealed), nonce, derivation, verifier link
```
- **Audit (MUST):** persist an immutable event per transition; every settled bet links to the seed + nonce + input + config-version that produced it. Fairness *and* economy-forensics requirement.

### 2.11 Tech stack — the position (FastAPI)
Owner stack is Python-primary (CLAUDE.md), and the brief fixes the backend on **FastAPI**. This is the canonical choice; it overrides the Go/Node recommendation in the prior draft.

| Concern | Choice | Why / honest caveat |
|---|---|---|
| API + game services | **FastAPI** (async, Pydantic v2) | One language for engine, math harness, and verifier; excellent typed validation at the boundary |
| Outcome engine | **Pure-Python `engine/` package** (no FastAPI imports) | Same discipline the React draft used: outcome logic is framework-free and unit-testable; the verifier imports it directly for guaranteed parity |
| Realtime (Crash, Poker) | **Starlette WebSockets** + **Redis pub/sub** | Fan-out round/table state across instances. *Caveat:* for very high-frequency Crash ticking, a **Go** sidecar on the hot path is the production move — not required at portfolio scale; documented as a seam |
| Data | **Postgres** (SQLAlchemy 2.0 async + Alembic) | ACID where credits live; the ledger is the trust core |
| Ephemeral state | **Redis** | Round/table state, rate limits, leaderboards (sorted sets), jackpot counters, daily-bonus timers |
| Fairness | Dedicated stateless module implementing §2.3 | Isolatable; reused by the verifier |
| Client | Thin web renderer (React/TS; canvas/PixiJS for Plinko/Crash/slots juice) | Presentation only — **never** authoritative. Engine choice is a presentation decision, validated by a vertical-slice spike |
| Packaging | Docker + docker-compose (Postgres + Redis + API); CI = lint + tests + **RTP gate** | Reproducible; the RTP gate is the headline test (§ tests) |

**One authoritative language rule.** Outcome derivation lives in the Python `engine/` package and is reused verbatim by the verifier, so server result == verifier result by construction.

---

# PART A — In-house Originals (provably fair, build from scratch)

**Per-game template:** Summary · Rules · Math & RTP (✅ verified) · Payout table · RNG mapping · State machine · API · Tests. All RTP = `1 − edge`; default `edge = 0.01`; set `edge = 0` for a 100%-RTP mode. **All Part A math was re-verified this session — 43/43 checks, Appendix A.**

Roster: **Dice, Pocket Dice, Limbo, Crash, Plinko, Mines, HiLo, Keno, Roulette (0–99)** + optional extensions (A.11). These are the showcase of the build — provable fairness + verified math live here.

## A.1 Dice ✅
**Summary.** Pick a number 1–100, bet **Over** or **Under**; lower win-probability → higher multiplier. The canonical crypto-casino primitive.

**Rules.** Player picks `target` and `OVER|UNDER`. Server rolls `roll ∈ [0.00, 100.00)` uniform. `UNDER` wins if `roll < target`; `OVER` wins if `roll > target`. Modes: Manual, Auto (base bet, on-win/on-loss multiply, stop-on-profit/loss, max rounds — RG-bounded), Risk (double-or-nothing).

**Math & RTP.** For win prob `p`: `multiplier = (1−edge)/p`, `RTP = p·multiplier = 1−edge`. `UNDER`: `p = target/100`; `OVER`: `p = (100−target)/100`. ✅ VERIFIED.

| Win chance p | Multiplier (edge 1%) | Example |
|---|---|---|
| 0.02 | 49.50× | UNDER 2 / OVER 98 |
| 0.25 | 3.96× | UNDER 25 |
| 0.50 | 1.98× | UNDER 50 |
| 0.75 | 1.32× | UNDER 75 |
| 0.98 | 1.0102× | UNDER 98 |

**RNG mapping.** 1 `randomFloat` → `roll = floor(f * 10000) / 100`. Pure function of `(serverSeed, clientSeed, nonce)`.
**State machine.** `PLACED → (debit) → ROLLED → {WON|LOST}` — single atomic request/response.
**API.**
```
POST /games/originals.dice/bet
  { walletId, currency:"GOLD", stakeMinor, target:50.00, direction:"UNDER", clientSeedRef }
→ { betId, roll:42.31, win:true, multiplier:1.98, payoutMinor, nonce, serverSeedHash }
```
**Edge cases.** Define boundary (`roll == target`): model `[0,100)` continuous so `P(roll==target)=0` and `UNDER` wins on `roll < target`. Reject targets that push `p` out of `[0.0001, 0.98]`. Auto-mode stops on RG limit mid-run.
**Tests.** Empirical RTP over 1e7 rolls ∈ `1−edge ± 0.5%`; determinism; verifier parity; cap/limit enforcement; boundary.

## A.2 Pocket Dice ✅
**Summary.** Bet on the **sum of 2d6** (2–12); pick a target and Over/Under. Triangular distribution — distinct from Dice.
**Math & RTP.** 2d6 pmf out of 36: 2→1,3→2,…,7→6,…,12→1. `multiplier=(1−edge)/p`. ✅ VERIFIED RTP=0.9900 every target/direction.

| OVER target | p | mult || UNDER target | p | mult |
|---|---|---|---|---|---|---|
| over 2 | 0.9722 | 1.018× || under 12 | 0.9722 | 1.018× |
| over 4 | 0.8333 | 1.188× || under 10 | 0.8333 | 1.188× |
| over 7 | 0.4167 | 2.376× || under 7 | 0.4167 | 2.376× |
| over 9 | 0.1667 | 5.940× || under 5 | 0.1667 | 5.940× |
| over 11| 0.0278 | 35.640×|| under 3 | 0.0278 | 35.640× |

**RNG mapping.** Two `randomFloat` → `die = floor(f*6)+1`; `s = d1+d2`. Disallow `OVER 12` / `UNDER 2` (p=0). State machine/API/tests as Dice.

## A.3 Limbo ✅
**Summary.** Set a **target multiplier**; round generates `X`; win `stake × target` if `X ≥ target`. Same distribution family as Crash, single-player, instant.
**Math & RTP.** `P(X ≥ x) = (1−edge)/x`. Generate `X = max(1.00, floor((1−edge)/(1−f)*100)/100)`. For `target`: `winProb=(1−edge)/target`, payout `target`, RTP `1−edge`. ✅ VERIFIED (sim RTP ≈ 0.989–0.990; target 2× → winP 0.495).
**Payout examples (edge 1%).** 1.5×→winP 0.660; 2×→0.495; 10×→0.099; 100×→0.0099. Cap target at `maxMultiplier`.
**RNG mapping.** 1 `randomFloat`. **Reuse the exact generator as Crash (A.4).**
```
POST /games/originals.limbo/bet { walletId,currency,stakeMinor, targetMultiplier:2.00, clientSeedRef }
→ { betId, generated:3.71, win:true, multiplier:2.00, payoutMinor, nonce }
```

## A.4 Crash ✅ (multiplayer, realtime — the hardest build)
**Summary.** Shared round: a multiplier curve rises from 1.00× and "crashes" at a random point. Players bet before the round, then **cash out** before the crash to win `stake × multiplier-at-cashout`. Everyone shares one curve.
**Rules.** Loop: **betting window** (~5s) → **running** → **crash** → **payout** → next. During betting, players place 1–2 bets with optional **auto-cashout**. While running, **manual cash out** anytime ≥1.00×. Crash before cash-out → lose.
**Math & RTP.** Crash point `C`: `P(C ≥ x) = (1−edge)/x`, generator identical to Limbo but seeded **per round** (§2.4):
```
f = randomFloat(roundServerSeed, clientSeed=roundId, nonce=roundNumber, cursor=0)   # roundId = public round salt
C = max(1.00, floor((1-edge)/(1-f) * 100)/100)
```
**Per-round commit–reveal (MUST).** Crash uses a per-ROUND seed (not the per-user `(serverSeed, clientSeed, nonce)` of the other Originals). The round's `serverSeedHash` is broadcast in `WAITING` (commitment); the `serverSeed` is revealed at crash. The open verifier reproduces a round via its **`round` mode** — `(roundServerSeed, roundId, roundNumber)` → `f` → `C` — distinct from the per-user Originals signature.
**Instant-bust** `P(C = 1.00) = edge` — the house edge is realized as rounds that crash at 1.00×. Cashing at target `t`: win iff `C ≥ t`, prob `(1−edge)/t`, RTP `1−edge`. ✅ VERIFIED (Monte Carlo, Appendix A).
**Curve presentation (cosmetic only).** `m(τ)=e^{kτ}` rendered client-side; the **server** is source of truth for the current value and crash instant. Clients reconcile to server ticks; never let client time decide a cash-out.
**State machine (round).**
```
WAITING(betting) → LOCKED(bets closed) → RUNNING(cashouts accepted)
  → CRASHED(C reached; settle losses; pay auto-cashouts that triggered < C) → SETTLED → next
```
Per-bet: `PLACED(debited) → ACTIVE → {CASHED_OUT(credit) | LOST}`.
**Cash-out integrity (critical).** Server-decided: client sends "cash out now"; server stamps the authoritative multiplier at receipt; if already ≥ C → loss. Auto-cashout target `t` wins iff `t ≤ C`, evaluated server-side — eliminates latency advantage.
**Realtime API (WebSocket).**
```
WS /games/originals.crash
  ← {type:"round", state:"WAITING", roundId, serverSeedHash, betCloseAt}
  → {type:"bet", stakeMinor, currency, autoCashout:2.00}
  ← {type:"betAck", betId}
  ← {type:"tick", multiplier:1.83}                  # throttled, advisory
  → {type:"cashout", betId}
  ← {type:"cashoutAck", betId, multiplier:1.85, payoutMinor}
  ← {type:"crash", roundId, crashPoint:1.97, serverSeed}   # reveal
```
**Concurrency & recovery.** Round is a single authoritative actor (one process/partition) fanning out via Redis pub/sub. On restart mid-round, the committed `roundSeed` already fixes `C`; replay settles deterministically.
**Tests.** Distribution of `C` matches `P(C≥x)=(1−edge)/x` over 1e6 rounds; instant-bust rate ≈ edge; auto-cashout exactness; no double-credit on duplicate cash-out; reconnect/resume; latency fairness; recovery after mid-round restart.

## A.5 Plinko ✅
**Summary.** Drop a ball through `R` rows of pegs into one of `R+1` bins, each with a multiplier. Bin ~ Binomial(R, ½). Risk level + rows reshape the curve.
**Math & RTP.** Bin `i` prob `P(i)=C(R,i)/2^R`. Multiplier set `{m_i}` chosen per `(rows,risk)` so `Σ P(i)·m_i = 1−edge` (symmetric, convex — high at edges). ✅ VERIFIED: a tuned R=16 set hits RTP exactly pre-rounding.
> **Rounding caveat (carried + confirmed):** 2-dp rounding shifts RTP slightly; **tune the published table so post-rounding RTP equals target** (optimize integer/2-dp multipliers against `Σ P(i)m_i = 1−edge`). Treat any example numbers as worked examples, not the final table.
**RNG mapping.** Consume `R` floats (one bounce each): `rightBounces = Σ[randomFloat < 0.5]`; `bin = rightBounces`. The rendered path is advisory; the **bin** is authoritative and must match.
```
POST /games/originals.plinko/bet { walletId,currency,stakeMinor, rows:16, risk:"HIGH", balls:1 }
→ { betId, results:[{ bin:3, path:[L,R,R,…], multiplier:5.94, payoutMinor }], nonce }
```
**Tests.** Bin frequencies match Binomial(R,½); **published-table RTP within ±0.2% of target after rounding** for every (rows,risk); determinism; verifier parity.

## A.6 Mines ✅
**Summary.** 5×5 grid, `M` hidden mines (1–24). Reveal safe tiles to grow the multiplier; one mine ends the round. Cash out anytime.
**Math & RTP.** After `k` safe reveals (N=25):
```
fairMultiplier(k) = C(N,k) / C(N−M,k)
payout(k)         = (1 − edge) · fairMultiplier(k)
P(survive k)      = 1 / fairMultiplier(k)
EV(cash after k)  = P(survive k) · payout(k) = 1 − edge      # exact, any k, any M
```
✅ VERIFIED: EV = 0.9900 across M∈{1,3,5}, k∈{1..5}.
> **Rounding note.** This EV is exact **pre-rounding**; with floor rounding (§2.1) the realized RTP is `≤` target. RTP sims therefore use stakes where the rounding bias is `≪` tolerance (§ tests).

| k | M=1 | M=3 | M=5 |
|---|---|---|---|
| 1 | 1.031× | 1.125× | 1.238× |
| 2 | 1.076× | 1.286× | 1.563× |
| 3 | 1.125× | 1.479× | 1.997× |
| 4 | 1.179× | 1.712× | 2.585× |
| 5 | 1.238× | 1.997× | 3.393× |

**RNG mapping.** Mine positions = `sampleWithoutReplacement(25, M)` from the fairness stream **at round start** (committed before any reveal). Reveals just look up whether a cell is a mine — **outcome fixed at round start**, not at click time (anti-cheat + provably fair).
**State machine.** `PLACED → (debit) → ACTIVE(reveals…) → {CASHED_OUT(credit) | LOST}`. Cash-out requires ≥1 reveal.
```
POST /games/originals.mines/bet    { walletId,currency,stakeMinor, mines:3 } → { roundId, gridSize:25 }
POST /games/originals.mines/action { roundId, op:"reveal", cell:12 }
   → { cell:12, safe:true, k:1, currentMultiplier:1.125, nextMultiplier:1.286 }
POST /games/originals.mines/action { roundId, op:"cashout" } → { status:"CASHED_OUT", multiplier, payoutMinor }
```
**Tests.** Survival/EV exactness per (M,k); layout determinism & verifier parity; can't reveal post-terminal; single-debit; serialize rapid reveals (no double-advance); cap enforcement.

## A.7 HiLo ✅
**Summary.** A card is shown; predict whether the next is **Higher-or-same** or **Lower-or-same**. Correct calls compound the multiplier; cash out anytime.
**Math & RTP.** Ranks A(1)…K(13), independent draws (document if drawn-without-replacement instead). For shown rank `r`:
```
p(Higher-or-same) = (14 − r)/13
p(Lower-or-same)  = r/13
stepMultiplier    = (1 − edge) / p(chosen)
cumulative        = Π step multipliers
```
Each step RTP = `1−edge`. ✅ VERIFIED.
> **Rounding note.** Step EV is exact **pre-rounding**; with floor rounding (§2.1) realized RTP is `≤` target (compounding repeats the floor each step). RTP sims use stakes where the rounding bias is `≪` tolerance (§ tests).

| Card | p(High≥) | ×High | p(Low≤) | ×Low |
|---|---|---|---|---|
| A | 1.000 | 0.990× | 0.077 | 12.870× |
| 5 | 0.692 | 1.430× | 0.385 | 2.574× |
| 7 | 0.538 | 1.839× | 0.538 | 1.839× |
| 9 | 0.385 | 2.574× | 0.692 | 1.430× |
| K | 0.077 | 12.870× | 1.000 | 0.990× |

**RNG mapping.** Each next card: 1 `randomFloat` → `cardIndex = floor(f*52)`. Whole sequence derives from `(serverSeed, clientSeed, nonce, step)` → verifiable.
**State machine.** `PLACED → (debit) → ACTIVE(guess→reveal→…) → {CASHED_OUT(credit) | LOST}`. Tie counts as a win for the chosen side (price `p` accordingly).
**Tests.** Per-rank step probs & multipliers; cumulative compounding; full-sequence determinism; verifier parity; cap.

## A.8 Keno ✅
**Summary.** Pick up to 10 numbers from a **1–40** grid; 10 are drawn; payout scales with hits per a risk-based table.
**Math & RTP.** Hits are **hypergeometric**: `P(hits=h|picks=k) = C(k,h)·C(40−k,10−h)/C(40,10)`. Each `(picks,risk)` has a payout vector `{pay_h}` chosen so `Σ_h P(h)·pay_h = 1−edge`. ✅ VERIFIED method; example pick-10 table `[0,0,0,0,1.75,8.77,35.08,175.39,876.95,3507.79,17538.95]` → RTP 0.9895 rounded (**tune to hit target**).
**RNG mapping.** `sampleWithoutReplacement(40, 10)`; compute hits vs picks.
```
POST /games/originals.keno/bet { walletId,currency,stakeMinor, picks:[3,7,11,…], risk:"MEDIUM" }
→ { betId, drawn:[…10…], hits:5, multiplier:8.77, payoutMinor, nonce }
```
**Tests.** Hypergeometric frequencies; per-(picks,risk) **table RTP within ±0.2% after rounding**; draw determinism & verifier parity; selection validation (`1 ≤ |picks| ≤ 10`, distinct, in-range).

## A.9 Roulette (Originals 0–99) ✅
**Summary.** A **0–99, 100-outcome** pick game with colour bets — **not** a European wheel (that's Part B B.3). Distinct, simpler, provably fair.
**Math & RTP.** `payout = (1−edge)/p`. ✅ VERIFIED:

| Colour | Pockets | p | Pays | RTP |
|---|---|---|---|---|
| Green / Joker | 1 | 0.01 | **99.00×** | 0.99 |
| Red | 49 | 0.49 | 2.0204× | 0.99 |
| Black | 50 | 0.50 | 1.98× | 0.99 |

**RNG mapping.** 1 `randomFloat` → `result = floor(f*100)` (0–99); map result→colour via the configured table; settle each placed bet.
```
POST /games/originals.roulette/bet { walletId,currency, bets:[{type:"COLOUR", value:"RED", stakeMinor:100}] }
→ { betId, result:42, colour:"RED", settlements:[{bet:0, win:true, multiplier:2.0204, payoutMinor:202}], nonce }
```
**Tests.** Result uniform over 0–99; per-bet RTP=1−edge with the configured colour map; determinism; verifier parity.

## A.10 Jackpot subsystem (optional, shared)
Independent, randomly-triggering jackpots funded by a slice of bets. **Optional for MVP.** If included: pop decision MUST be provably fair (committed to the fairness chain), and the pool MUST reconcile to the cent (Redis counters + durable Postgres snapshots). Account `jackpotRake` inside the edge budget so total RTP incl. jackpot still equals target.

## A.11 Optional extensions (house additions — label clearly)
Same engine/fairness contracts; `payout=(1−e)/p` pattern.

| Game | Model (edge `e`) | RTP |
|---|---|---|
| **Towers / Dragon Tower** | rows of `T` tiles, `S` safe; after k rows `mult=(1−e)·(T/S)^k`; survive `(S/T)^k` | 1−e ✅ |
| **Wheel** | segmented; per-segment `mult` tuned so `Σ p·m = 1−e` (like Plinko) | 1−e |
| **Coinflip** | 50/50 → `1.98×` | 1−e ✅ |
| **Diamonds / Slide** | combinatorial reveal; `payout=(1−e)/p` | 1−e |

---

# PART B — In-house classic games (slots, table, video poker)

These are server-authoritative + CSPRNG + audited (§2.3c). Extending provably-fair commit–reveal to them (committed shoe/reel stops) is **recommended** and cheap. Unlike Part A, several house edges below are **standard published values** (textbook math), *not* re-simulated this session — flagged accordingly.

## B.1 Slots framework (the social-casino revenue engine)
**Build a data-driven framework, not individual games.** A new machine should be **config + art + audio**, with code only for genuinely novel mechanics. This is the highest-leverage thing in Part B.

**Config (per machine, versioned in `GameConfig`):**
- Grid (e.g. 5 reels × 3 rows), **weighted reel strips** (symbol frequency per reel — this is what sets RTP and volatility), symbol set, **paytable** (symbol × count → multiplier), lines or **ways-to-win** (e.g. 243 ways), wild/scatter rules, and **feature modules** (free spins, hold-and-spin, pick bonus, coin "jackpot" pots).
- Target **RTP** (0.92–0.96 typical for social) and **volatility** profile.

**Outcome (server).** CSPRNG picks a stop index per reel from that reel's strip → grid → evaluate lines/ways → sum base wins → trigger/evaluate features → total multiplier. RTP emerges from strip weights + paytable, **not** from a post-hoc clamp.

**RTP verification = CI gate.** Each machine/config-version runs a large-N spin simulation in CI; the build **fails** if measured RTP deviates from target beyond tolerance. **Counts (matching the kit):** **1e7 on PR** (fast subset; tolerance = a CLT/binomial confidence half-width, not a flat ±0.5%), **1e8 nightly/manual** in the dedicated `rtp-gate` job. Vectorized (numpy) sims are allowed **in tests only**, never in the engine. Never widen a tolerance to pass. (This gate is a headline portfolio artifact — it proves the math, automatically, per machine.)

```
POST /games/slots.{machineId}/spin { walletId, currency, stakeMinor, lines:20 }
→ { betId, grid:[[…],[…],…], lineWins:[{line:3, symbols:"AAA", multiplier:5}],
    features:{ freeSpinsAwarded:0 }, totalMultiplier:1.5, payoutMinor, nonce }
```
**Tests.** Per-machine RTP sim within tolerance; reel-strip frequency check; feature-trigger rate; paytable evaluation correctness; determinism + (optional) verifier parity; max-win cap.

## B.2 Blackjack
**Rules.** `D`-deck shoe (default 6), **dealer stands on soft 17** (configurable; H17 raises edge), player actions hit / stand / double / split / insurance / (optional) surrender, **blackjack pays 3:2**, reshuffle at a penetration threshold. House edge **≈ 0.5% with basic strategy** — *rules-dependent, standard published value, not re-simulated here* (⚠️ verify for the exact rule set you ship).
**Fairness.** Server-authoritative draws from a committed/CSPRNG shoe (provably-fair shoe optional).
**State machine.** `BETTING → DEAL → PLAYER_ACTIONS(per hand; splits create hands) → DEALER_PLAY → SETTLE`.
```
POST /games/blackjack/bet    { walletId,currency,stakeMinor } → { roundId, player:[…], dealerUp:"♠K" }
POST /games/blackjack/action { roundId, op:"HIT|STAND|DOUBLE|SPLIT|INSURANCE" } → { … }
```
**Tests.** Rules-engine property tests (bust, blackjack, push, payout, split/double resolution, dealer draw rules); basic-strategy EV sim ≈ published edge for the configured rules.

## B.3 European Roulette (single-zero wheel)
**Rules.** 37 pockets (0–36); standard inside/outside bets. Single-zero → **every bet has edge 1/37 ≈ 2.70%** (RTP 36/37 = 0.9730). ✅ arithmetically exact (straight-up: payout 35:1, p=1/37 → RTP=36/37).

| Bet | Payout | p | RTP |
|---|---|---|---|
| Straight (1 number) | 35:1 | 1/37 | 0.9730 |
| Split (2) | 17:1 | 2/37 | 0.9730 |
| Red/Black, Odd/Even, High/Low | 1:1 | 18/37 | 0.9730 |
| Dozen / Column | 2:1 | 12/37 | 0.9730 |

**RNG mapping.** 1 `randomFloat` → `pocket = floor(f*37)`; settle each placed bet via the payout table.
**Tests.** Pocket uniform over 0–36; per-bet RTP = 36/37; multi-bet settlement; determinism.

## B.4 Baccarat (punto banco)
**Rules.** Bet Player / Banker / Tie; fixed third-card drawing rules (no player decisions). **Standard published edges (not re-simulated here, ⚠️ depend on deck count / 5% banker commission / tie payout):** Banker ≈ **1.06%**, Player ≈ **1.24%**, Tie (8:1) ≈ **14.36%**. (Computable by exact shoe enumeration if you want to certify.)
**Fairness.** Committed/CSPRNG shoe; deterministic drawing-rules engine.
**Tests.** Third-card rules engine exhaustively correct; edge by enumeration matches published values for the configured rules.

## B.5 Video Poker (e.g. 9/6 Jacks or Better)
**Rules.** 5-card draw from a 52-card deck; player holds, redraws; **paytable-driven**. 9/6 Jacks-or-Better ≈ **99.54% RTP with optimal play** — *standard published value, paytable- and strategy-dependent, not re-simulated here.*
**Fairness.** Deal from committed/CSPRNG deck; redraw consumes further cards from the same committed stream.
**Tests.** Hand-rank evaluation correctness; paytable RTP under optimal (or specified) strategy ≈ published; deck-stream determinism.

---

# PART C — Poker (PvP Texas Hold'em, realtime)

**The most backend-intensive game; build it last (it depends on a proven realtime foundation — Crash).** Strongest social-engagement driver (friends, clubs, tournaments).

**Scope.** Cash tables (No-Limit Hold'em): lobby + **matchmaking**, seats/blinds/button, four betting rounds (preflop/flop/turn/river), **main + side pots**, 7-card **hand evaluator** (best-5), showdown, **rake** (play-money rake is an economy sink; rake-free is a config option), **turn timers**, **disconnect handling** (auto-check/fold or sit-out, auto-muck), reconnection/resync. Tournaments (SNG/MTT) are a later extension.

**Realtime architecture.** WebSocket gateway; **one authoritative table actor** serializes all actions for a table; table state in **Redis**, checkpointed to Postgres; fan-out via Redis pub/sub. Each player sees only their own hole cards + public state — the server **never** sends unseen cards to a client.

**Integrity / anti-collusion (the hard part).**
- Server-authoritative shuffle (CSPRNG; optional committed-deck provably-fair shuffle revealed post-hand).
- No hole-card leakage — per-seat redaction enforced server-side, tested explicitly.
- **Anti-collusion** signals: chip-dumping (consistent one-way transfers), players who only ever enter pots against non-allies, soft-play (fold-to-each-other rates), shared-IP/device at a table, suspicious timing correlation. Emit to the risk log (§2.7).

**API (WebSocket).**
```
WS /poker/table/{tableId}
  → {type:"join", seat:3, buyInMinor}
  ← {type:"state", seats, button, blinds, pot, toAct, yourHole:["A♠","K♠"]}
  → {type:"act", action:"RAISE", amountMinor}
  ← {type:"act.ack"} / {type:"street", cards:["7♦","2♣","J♠"]} / {type:"showdown", …}
```
**Tests.** Hand-evaluator correctness (exhaustive rank ordering + known hands); **side-pot math under multiple all-ins** (the classic bug source); blind/button rotation; timer + disconnect + reconnect; **no-hole-card-leakage** test; rake accounting reconciles to the ledger.

---

# PART D — Third-party content integration (optional)

For a play-money portfolio this is **optional and likely deferred** — most aggregators/providers are real-money and contract-gated. Documented for completeness and for the real-money extension path.

- **Topology.** Your lobby → provider/aggregator **RGS** (renders game in iframe/redirect); provider calls **your seamless wallet** for balance/bet/win/rollback. Aggregator-first (one integration → many providers) beats per-provider for MVP.
- **Seamless wallet** mirrors §2.2 rules (idempotency keyed on `(provider, providerTxId)`, atomic, single source of truth, signature/HMAC + IP allowlist, nightly reconciliation).
- **Demo/fun mode** uses provider play-credits — a natural fit for a play-money product and pre-login browsing.
- **Fairness/cert** is the provider's responsibility; **your integration** (wallet, geo, RG) is in your audit scope.

---

# 5. Real-money extension & compliance awareness

This product **ships play-money, non-redeemable.** Real money is a **designed-for seam, not enabled.** Documenting it is part of the showcase (it proves you understand the regulated context), but none of it is wired in v1.

**Real-money blocking dependencies (if ever flipped on):**
1. **Licence** (MGA / Curaçao / local) — defines markets, RTP disclosure, RG, audit.
2. **RNG + game-math certification** by an accredited lab (GLI-19 interactive systems, GLI-11 RGS; iTech Labs / eCOGRA / BMM). The Part A math is built to pass; it still must be lab-tested.
3. **KYC/AML** on `debit` (real mode only).
4. **Geo/jurisdiction** blocking at session + launch.
5. **Full responsible-gaming** service (deposit/loss/wager/session limits, reality checks, cool-off, self-exclusion + national registers).

**Social-casino legal awareness** *(carried from the prior social-casino draft; ⚠️ NOT re-verified this session; not legal advice — retain specialised gaming counsel before any launch):*
- **Washington State** treats virtual chips as a "thing of value" — *Kater v. Churchill Downs Inc.*, 9th Cir. 2018 (886 F.3d 784), and a later High 5 Games ruling. Highest-risk US state for social casino.
- A **2025 wave of state action** targets the **sweepstakes** (redeemable) social-casino model (e.g. a reported California ban; activity in other states). This plan's **non-redeemable** stance is precisely what stays out of that crosshair.
- **Apple** requires simulated-gambling apps to carry a **17+** rating and disclose that no real money is involved (Guideline 5.3); Google Play has parallels. Applies even with no real money — relevant if you wrap native apps.

> The honest engineering takeaway: **non-redeemable play-money keeps v1's legal surface small**, and the engine is built so a licensed real-money mode is a configuration + compliance project, not a rewrite.

---

# 6. Build sequence (summary — full plan in build-plan_v2)

```
Phase 0  Platform spine: wallet/ledger (double-entry) + RNG/provably-fair + verifier + limits/RG hooks + data model + audit + CI(RTP gate)
Phase 1  First Originals (instant): Dice → Limbo → Pocket Dice → Mines → Plinko → HiLo → Keno → Roulette(0–99)
Phase 2  Crash (realtime multiplayer; WebSocket round actor; auto-cashout integrity; recovery)
Phase 3  Slots framework (data-driven; per-machine RTP CI gate) + 2–3 machines
Phase 4  Table/video poker (Blackjack → European Roulette → Baccarat → Video Poker)
Phase 5  Poker PvP (realtime tables, hand evaluator, side pots, anti-collusion)
Phase 6  Economy + LiveOps + polish + portfolio packaging (README, diagrams, deployed demo)
```
**Smallest impressive slice:** Phase 0 + Dice + Mines + Crash (play-money, provably fair, verifier, RTP gate). See `2026-06-27_casino-games_build-plan_v2.md` for milestones, dependency graph, the scope reality-check, and effort.

---

# 7. Honesty log / open questions

1. **Originals parameters** (rosters, exact published multiplier tables for Plinko/Keno/Wheel) are modeled on standard crypto-casino designs; the *method* is verified, the *exact published numbers* must be tuned (and re-checked if cloning a specific operator). ⚠️
2. **Part B table-game edges** (Blackjack ~0.5%, Baccarat 1.06%/1.24%/14.36%, Video Poker 99.54%) are **standard published values, not re-simulated this session** — they depend on the exact rules/commission/paytable you ship. Verify by enumeration/sim before locking.
3. **Legal claims (§5)** are carried from the prior vault draft and **not independently verified this session**. Not legal advice.
4. **Stack:** FastAPI is fixed per brief. A **Go sidecar** for the Crash tick/cash-out hot path is the production option, not built at portfolio scale — documented as a seam (§2.11).
5. **Provably-fair for Part B/C** is recommended but optional; v1 may ship server-authoritative + audit only.
6. **Real-money mode** is unwired by design; do not represent this build as licensable without §5's dependencies.

---

# Appendix A — Verified math (re-run this session)

Re-verified independently in-session (analytic enumeration + Monte Carlo): **43/43 checks passed.** Target RTP at edge=1% is **0.9900** (exactly 1.0000 at edge=0).

> **Status of "43/43".** This is a **prior, in-session manual** verification (a one-time check, not in the repo). The **reproducible, in-repo proof** is the CI RTP gates wired from **S7** onward (`tests/test_*_rtp.py`, the `rtp-gate` job) — those are what actually keep the math honest per build; the table below is the human-readable record they back.

```
DICE     p∈{.02,.25,.50,.75,.98} → mult {49.50,3.96,1.98,1.32,1.0102}× ; RTP 0.9900 (analytic) ✅
POCKETD  2d6 pmf; OVER/UNDER all targets RTP 0.9900 ✅ (over7/under7 2.376×)
LIMBO    P(X≥x)=(1-edge)/x ; sim RTP 0.989–0.990 (2M trials/target); target2× winP 0.495 ✅
CRASH    = Limbo distribution; instant-bust P(C=1)=edge ✅
MINES    payout=(1-e)·C(25,k)/C(25-M,k); EV 0.9900 exact, M∈{1,3,5} k∈{1..5} ✅
         M=3: k1 1.125× k2 1.286× k3 1.479× k4 1.712× k5 1.997×
ROULETTE 0–99: green1 99.00× | red49 2.0204× | black50 1.98× ; each RTP 0.9900 ✅
PLINKO   R=16 binomial; tuned set hits RTP 0.9900 exactly pre-rounding; re-tune after 2dp rounding ✅(method)
KENO     pick10/40 draw10 hypergeometric; example payouts → RTP 0.9895 rounded (tune to target) ✅(method)
HILO     p(High≥r)=(14-r)/13; per-rank mult; each step RTP 0.9900 ✅ (A 12.870× low / K 12.870× high)
--- standard published (NOT re-simulated this session) ---
EURO ROULETTE  37 pockets, every bet edge 1/37 = 2.70%, RTP 0.9730 (arithmetic ✅)
BLACKJACK      ~0.5% edge, rules-dependent (basic strategy)
BACCARAT       banker 1.06% / player 1.24% / tie(8:1) 14.36% — commission/payout-dependent
VIDEO POKER    9/6 Jacks-or-Better ≈ 99.54% optimal play — paytable/strategy-dependent
```

# Appendix B — Glossary
**RTP** return to player = 1 − house edge. **Edge** house margin (fraction). **Provably fair** cryptographic commit–reveal letting players verify outcomes. **Server/Client seed, Nonce** inputs to the per-bet RNG. **CSPRNG** cryptographically secure RNG. **Double-entry ledger** append-only record where every credit movement debits one account and credits another; balances are reconciled projections. **Server-authoritative** the server decides every outcome; the client only renders. **Ways-to-win** slot payout model (any-adjacent-reel) vs fixed paylines. **Volatility** payout variance shape. **Rake** poker house cut. **Side pot** poker pot formed when a player is all-in for less. **RGS** Remote Game Server. **Seamless wallet** provider-initiated bet/win callbacks. **GLI-11/GLI-19** lab standards for gaming devices / interactive systems.

---

## Related vault notes
[[provably-fair]] · [[double-entry-ledger]] · [[server-authoritative]] · [[rng-certification]] · [[house-edge]] · [[crash-game-architecture]] · [[slot-math-model]] · [[poker-hand-evaluator]] · [[fastapi]] · [[igaming-compliance]] · [[social-casino]]

*Spec v2 generated 2026-06-27 — merge of three prior drafts (see frontmatter `supersedes_drafts`). Originals math re-verified in-session (Appendix A, 43/43). Table-game edges and legal claims are standard/carried values, flagged in §7. Money model: social play-money, non-redeemable; real-money documented as an extension only.*
