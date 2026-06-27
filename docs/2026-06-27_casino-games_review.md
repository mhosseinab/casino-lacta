---
type: review
project: casino-games
codename: lacta
tags: [casino, review, gap-analysis, pre-build, dev]
status: APPLIED 2026-06-27 — all 15 findings fixed in-place across the kit
slug: casino-games
date: 2026-06-27
reviews: [CLAUDE.md, 2026-06-27_casino-games_spec_v2.md, 2026-06-27_casino-games_build-plan_v2.md, 2026-06-27_casino-games_plan.md, 2026-06-27_casino-games_implementation-steps.md, 2026-06-27_casino-games_orchestrator-prompt.md, 2026-06-27_casino-games_monorepo-blueprint.md, 2026-06-27_casino-games_progress.md, .claude/agents/*, .claude/skills/*]
---

# Casino (lacta) — Pre-build Doc Review & Gap Analysis

> **✅ RESOLUTION (2026-06-27).** All 15 findings below were **fixed in-place** across the kit
> (`CLAUDE.md`, all 7 docs, the 5 reviewer agents, and the `add-game`/`rtp-harness` skills). This
> document is retained as the record of *what* changed and *why*. Each finding's "Fix" is now
> reflected in the source docs; a final consistency sweep confirmed zero residual
> `Projects/casino-games/` paths, no `{S20,S35}` deploy edges, the canonical purity grep/import-linter
> list everywhere, and `mypy engine app verifier` throughout. Canonical decisions locked: repo root
> `casino-lacta/` (branch `casino-games/main`); `SYSTEM`/house counterparty account + system-wide
> `Σ(entries)==0`; `apply_multiplier` floors; DB `GameConfig`/`GameLimit` authoritative + seeded;
> Crash per-round-seed fairness with a verifier `round` mode; tiered RTP gate (1e6 PR / 1e7–1e8
> nightly); client out of the S1–S42 critical path.

**Scope.** A consistency + completeness pass over the whole build kit (7 docs + `CLAUDE.md` + 5
reviewer agents + 3 skills) before S1 runs. This is **propose-only** — no kit doc was edited. Each
finding cites where it lives and a concrete fix.

**Verdict.** The kit is unusually well-built: the engine-purity boundary, double-entry/idempotency
discipline, RTP-as-CI gate, branch-per-step orchestration, and 5 tailored security reviewers are
coherent and mutually reinforcing. The gaps below are **not** "this is wrong" — they are
**under-specifications and cross-doc inconsistencies that will cost rework if a worker hits them
mid-build**. Four are worth resolving *before* S1/S2/S3 because they shape the schema and the money
math; the rest can be fixed as their phase approaches.

> **Out of scope / already self-identified:** the blueprint §7 production-readiness checklist
> already catalogs the real *production* gaps (rate limiting, secrets management, backups/PITR,
> tracing, GDPR delete endpoint, partition routing for >1 instance, contract-drift check). I have
> **not** re-list those as findings — they are known and correctly deferred. This review is about
> internal correctness/consistency of the *kit itself*.

Severity legend: **P0** resolve before S1–S3 (foundational; wrong → schema/ledger rework) · **P1**
resolve before the relevant phase · **P2** consistency/polish.

---

## P0 — Resolve before S1–S3

### P0-1 · Double-entry ledger has no counterparty ("house") account in the data model
**Where.** `spec_v2.md` §2.9 (entity list) and §2.2; built by S2 (schema) + S3 (ledger).
**What.** §2.2 states the ledger rule as "each movement **debits one account and credits another**
by equal magnitude." But §2.9's entities are `User, Wallet, LedgerEntry, GameRound, Bet, ServerSeed,
ClientSeed, NonceCounter, GameConfig, GameLimit, PokerTable, Jackpot, AuditEvent` — there is **no
house/system account or wallet**. The only `Wallet` is `Wallet(userId, …)`. So the "credits another"
side of every wager/payout has no modeled home. Downstream steps already assume one exists: S33
("rake is an economy sink **booked to the house** via the ledger"), S37 ("sinks flow to **the house
account** in the ledger"), and the chips-in==chips-out conservation the reviewers check.
**Why it matters.** A worker on S2/S3 will either (a) invent an ad-hoc house account inconsistently,
(b) silently degrade to single-entry-with-running-balance (the `LedgerEntry.balanceAfter` column
nudges toward this), or (c) make rake/grants "appear from nowhere," breaking system-wide
conservation. This is the single highest-leverage thing to pin down, because it's a schema decision.
**Fix (decide one, then write it into §2.2/§2.9 + S2/S3):**
- Add a **system account** to the model — simplest: a reserved `User`/`Wallet` per currency with
  `userId = SYSTEM` (house), so every `debit`/`credit`/`grant`/`rake` is a balanced two-row
  `LedgerEntry` set (`txnId` links them; player row + house row sum to zero).
- State the invariant explicitly: `reconcile()` is per-wallet (`balance == Σ entries`) **and** a
  system-wide check holds `Σ all entries == 0` (true double-entry). Add the system-wide assertion to
  S3's tests and the ledger-security-reviewer checklist.
- If you instead intend **single-entry-with-running-balance** (one row per wallet, `balanceAfter`),
  then say so and stop calling it "double-entry" in §2.2/CLAUDE.md — and define where rake/grants
  source from. (I'd keep true double-entry; it's the portfolio claim.)

### P0-2 · Repo path & codename drift; S1's "create repo / init git" doesn't match reality
**Where.** `CLAUDE.md` (header), `monorepo-blueprint.md` §3 (`casino-lacta/`, pnpm pkg `@lacta/contracts`),
`plan.md`/`steps.md`/`orchestrator-prompt.md` (all say `Projects/casino-games/`), S1 prompt.
**What.** Three names are in play — **`casino-games`** (project/branch `casino-games/main`),
**`casino-lacta`** (blueprint repo root + the actual workspace folder), **`lacta`** (codename, pnpm
scope `@lacta/`). And the ground truth I verified this session: the repo **already exists** at
`…/workspace/casino-lacta`, is **already `git init`-ed** (branch `master`, **0 commits**), and
already contains `docs/`, `.claude/`, `CLAUDE.md`. Yet S1 says *"Create the repo at
`Projects/casino-games/` … Initialize git; first commit on branch `casino-games/main`."*
**Why it matters.** A worker following S1 verbatim will try to create a nested `Projects/casino-games/`
directory and re-init git, fighting the existing repo. The path/name mismatch also breaks the
orchestrator's "open a session at `Projects/casino-games/`" instruction.
**Fix.**
- Pick the canonical repo root = **the existing `casino-lacta/`** (it already holds the kit).
- Rewrite S1's opening to: *"In the existing repo root (`casino-lacta/`, already git-init-ed, no
  commits yet), create branch `casino-games/main` and make the first commit there. Do NOT create a
  nested project dir."*
- Global find/replace `Projects/casino-games/` → the repo root in `plan.md`, `steps.md`,
  `orchestrator-prompt.md`; and reconcile the branch name vs. folder name (keep branch
  `casino-games/main` if you like, but state that the *folder* is `casino-lacta`). Decide whether the
  pnpm scope is `@lacta/` or `@casino-games/` and make it consistent.

### P0-3 · `money.apply_multiplier` rounding rule is referenced everywhere but never specified
**Where.** `CLAUDE.md` ("`money.py#apply_multiplier` has an explicit rounding rule"), S5 ("explicit
rounding rule"), `engine-purity-reviewer` ("the project's explicit rounding rule"). Verified: the
docs contain `floor()` only for RNG mappings (roll/dice/pocket), **never** for payout rounding.
**What.** `apply_multiplier(stakeMinor:int, multiplier:float) -> int` must turn a float product into
integer minor units, but *how* it rounds (floor / round-half-up / round-half-even / ceil) is never
stated. It is called "explicit" in three places and defined in none.
**Why it matters.** (1) It's a correctness fork: floor is house-favorable, ceil player-favorable,
half-even neutral. (2) It directly biases RTP — the "EV = 0.9900 **exact**" claims for Mines/HiLo
(spec §A.6/§A.7) hold only with *no* rounding loss; with integer minor units + floor, small stakes
shed fractional cents and **measured RTP drifts below target**, which the ±0.5% gate may or may not
absorb depending on stake size used in sims. (3) Two engineers will pick differently → verifier
parity risk if app and a hand-check disagree.
**Fix.**
- State the rule in §2.1 (or a new §2.1a) and in S5, e.g.: *"`apply_multiplier` rounds **down**
  (`floor`) to the nearest minor unit; the truncated fraction is house margin. `cap(payout, maxWin)`
  applies after rounding."* (Floor is the conventional, defensible choice.)
- Add a sentence to the RTP-harness skill: RTP sims must use **stake sizes large enough that
  per-bet rounding bias ≪ tolerance** (or assert RTP within `[target − roundingBias, target]`), so a
  legitimate floor bias isn't misread as a math bug.
- Note in §A.6/§A.7 that EV is "exact pre-rounding; floor rounding makes realized RTP ≤ target."

### P0-4 · `GameConfig` / `GameLimit` DB rows are validated but never seeded; config authority is split
**Where.** `spec_v2.md` §2.5 (`game_limits` table), §2.9 (`GameConfig` versioned), S5 (engine
`registry.py` holds "default GameConfig"), S6 (bet loop "validate limits (GameLimit)" + audit links
"configVersion").
**What.** Two sources of game config coexist with no stated authority: the **engine `registry.py`**
default `GameConfig` (in code, S5) and the **DB `GameConfig`/`GameLimit`** tables (versioned,
hot-updatable, §2.9/§2.5). S6's bet loop validates `GameLimit` and writes `configVersion` into the
audit — but **no step ever creates a `GameLimit` or `GameConfig` row**, and nothing defines where
`configVersion` comes from if config lives in code.
**Why it matters.** S6's limit validation has nothing to read; every game step (S9–S28) will need
limits to exist for its bet path and RTP sim, and each worker will improvise seed rows differently.
The audit's `configVersion` is unsourced.
**Fix.**
- Decide authority: recommend **DB is authoritative at runtime; engine `registry` provides
  defaults/seed values.** Add to S2 (or a small new step S2.5) a **config/limit seeder** (Alembic
  data migration or a `seed_configs()` fixture) that populates `GameConfig` + `GameLimit` per game,
  and have each game step add its row.
- Define `configVersion` = `GameConfig.version`; the bet loop reads the active row and stamps that
  version into `AuditEvent`. State this in S6.

---

## P1 — Resolve before the relevant phase

### P1-1 · Engine-purity enforcement has holes — `random`/`secrets`/clock slip past the structural gates
**Where.** `monorepo-blueprint.md` §5 (import-linter contract), `CLAUDE.md` + S4/S5 Verify (the grep),
vs. `engine-purity-reviewer.md` (a stricter grep).
**What.** The "never `random`" rule is the load-bearing constraint, but the two *structural* gates
that are supposed to guarantee it are weaker than the manual reviewer:
- The **import-linter** `engine-is-pure` contract forbids `app, verifier, fastapi, sqlalchemy, redis,
  httpx` — it does **not** forbid `random`, `secrets`, `os`, `time`, `datetime`, `asyncio`. So
  `import random` in `engine/` **passes import-linter.**
- The **canonical grep** in `CLAUDE.md`/S4/S5 is `import (random|secrets|fastapi|sqlalchemy|redis)` —
  it misses `os`, `time`, `datetime`, `asyncio`, `pathlib`, `open(` (wall-clock!), which the iron
  rules forbid. Only the `engine-purity-reviewer` agent's private grep catches them.
**Why it matters.** A worker who runs the *documented* Verify can have `import time`/`import random`
in `engine/` and see green; the catch then depends entirely on a human reviewer remembering. The
structural guarantee the whole design leans on ("purity is a dependency fact") has a gap.
**Fix.**
- Add to `.importlinter` `engine-is-pure` `forbidden_modules`: `random, secrets, time, datetime, os,
  asyncio, pathlib` (and keep the existing). This makes the #1 rule structural.
- Promote the reviewer's stronger grep to be **the canonical one** in `CLAUDE.md` Commands and in the
  S4/S5 Verify blocks:
  ```bash
  git grep -nE "import (random|secrets|fastapi|sqlalchemy|redis|httpx|asyncio|os|time|datetime|pathlib)|(^|[^.])\bopen\(" -- engine/
  ```

### P1-2 · `S40` (load test) hard-depends on `S35` (anti-collusion) → cutting poker blocks deploy
**Where.** `steps.md` + `progress.md` DAG: `{S20,S35} → S40[SIGN-OFF] → S41[DEPLOY] → S42`.
**What.** Every doc says poker (P6 / S29–S35) is **the cuttable phase** ("can be cut entirely without
affecting P1–P5 value"). But S40's prerequisites are `{S20, S35}`, and S35 is the *last poker step*.
If poker is cut, S35 never reaches `passed`, so S40 is never eligible → **S41 deploy is
unreachable.** The DAG contradicts the stated "poker is optional" invariant.
**Why it matters.** The kit's headline de-risking move (ship P1–P5, defer poker) is silently blocked
by its own dependency graph.
**Fix.** Make S40 depend on **"the realtime phases that were actually built."** Concretely:
`S20 → S40`, and add S35 as a prerequisite **only if poker is in scope**. Reword S40: *"Depends on:
S20 (Crash); plus S35 if poker was built."* Update both DAGs and the `progress.md` graph.

### P1-3 · Orchestrator routing ignores the 5 tailored reviewer agents, and the routing lists disagree
**Where.** `orchestrator-prompt.md` (SETUP step 2 + REVIEW) routes only to `/security-review` and
`/code-review`; `progress.md` security list = `{S3,S4,S8,S19,S20,S32,S33,S35,S41}`; but `CLAUDE.md`
"Agents & skills" maps the **named** agents to a *different* step set.
**What.** Two problems:
1. The kit ships 5 purpose-built reviewers (`engine-purity-reviewer`, `ledger-security-reviewer`,
   `fairness-rng-reviewer`, `realtime-integrity-reviewer`, `frontend-renderer-reviewer`), but the
   thing you actually paste to drive the build — the orchestrator prompt — never names them. It says
   `/security-review` / `/code-review` with a generic-subagent fallback. **The tailored agents can go
   unused.**
2. The step→reviewer mappings disagree across docs:

   | Step | `CLAUDE.md` agents say | `progress.md`/orchestrator security list |
   |---|---|---|
   | S6 bet loop | ledger-security-reviewer | code-review (not listed) |
   | S7 verifier/RTP | fairness-rng-reviewer ("S4, **S7**") | code-review (not listed) |
   | S34 timers | realtime-integrity-reviewer ("S32–**S35**") | not listed |
   | S36/S37 faucets/leaderboards | ledger-security-reviewer | not listed |

**Why it matters.** S6 (the shared money path), S7 (the open verifier — the fairness surface), and
S36/S37 (grants/sinks) are exactly the diffs you want a security-grade reviewer on; the routing list
sends them to generic review.
**Fix.**
- In the orchestrator REVIEW step, route **by area to the named agents** (engine diffs →
  `engine-purity-reviewer`; `wallet`/`bet_loop`/`auth`/`economy`/poker-money → `ledger-security-reviewer`;
  `rng`/`fairness`/`verifier`/`/fairness` → `fairness-rng-reviewer`; `ws/`/poker realtime →
  `realtime-integrity-reviewer`; `client/` → `frontend-renderer-reviewer`), with `/code-review` as the
  fallback only.
- Publish **one** step→reviewer table (in `progress.md`) and make `CLAUDE.md` + orchestrator point to
  it. Add S6, S7, S34, S36, S37 to the security-grade set.

### P1-4 · Crash fairness doesn't fit the verifier's `(serverSeed, clientSeed, nonce)` signature
**Where.** `spec_v2.md` §2.3 (per-user serverSeed/clientSeed/nonce commit–reveal), §2.4
(`crashPoint(f,edge)` — but where does `f` come from for Crash?), §A.4, S7 (verifier signature),
S18 ("committed round seed").
**What.** Originals derive `f = randomFloat(serverSeed, clientSeed, nonce)` **per user**. Crash is
**multiplayer with one shared round seed** — `serverSeedHash` is broadcast in `WAITING`,
`serverSeed` revealed at `crash`. There is no per-user `clientSeed`/`nonce` for a Crash round, yet the
verifier (S7) is specified as `verify(serverSeed, clientSeed, nonce, input, gameId)`. **The spec
never says what plays the role of `clientSeed`/`nonce` (or `cursor` input) when deriving a round's
`f`.** So "a player verifies a Crash round" has no defined procedure.
**Why it matters.** Crash is the flagship realtime game and "provably fair" is the flagship claim;
the verifier must reproduce a Crash `C`, but the derivation input is under-specified. This will block
S18's parity/verifier story.
**Fix.** Specify Crash's derivation in §2.4/§A.4 explicitly, e.g.: `f = randomFloat(roundServerSeed,
clientSeed = roundId or a public round salt, nonce = roundNumber, cursor = 0)`, and extend the
verifier (S7) to accept a `round` mode. State the commit (`serverSeedHash` per round) and reveal
(at crash) timing as a distinct flow from the per-user Originals flow.

### P1-5 · RTP-gate CI runtime, and `1e7` (steps) vs `1e8` (spec) for slots
**Where.** `rtp-harness` skill + S9/S23 (`≥1e7`), `spec_v2.md` §B.1 (`e.g. 1e8` spins), S24 (machine03
"high volatility").
**What.** Two issues. (a) **Inconsistency:** steps say `≥1e7` spins for slot RTP; spec §B.1 says
`1e8`. (b) **Statistics + cost:** for a **high-volatility** machine (S24), `1e7` pure-Python spins may
not converge to **±0.5%** — a single rare jackpot swings the mean — and `1e8` Python spins per
machine, run in CI on every push, is *minutes-to-hours*. Nothing in the kit budgets RTP-gate wall
time or addresses convergence-vs-volatility.
**Why it matters.** Either the gate is too slow to run per-commit, or `1e7` is too few for high-vol →
flaky failures that tempt someone to "just widen the tolerance" (explicitly forbidden).
**Fix.**
- Reconcile the trial counts (pick `1e7` for PR/`pytest`, `1e8` for the heavy nightly) and say so in
  one place (the `rtp-harness` skill).
- Tier the gate: **fast subset on PR** (e.g. `1e6`, tolerance derived from a binomial/CLT confidence
  interval, not a flat ±0.5%), **full `1e7`–`1e8` nightly/manual** in the dedicated `rtp-gate` job.
- Allow `numpy`-vectorized sims **in `tests/`** (not `engine/` — purity unaffected) to make `1e8`
  feasible; note this in the harness skill. Express tolerance as a **CI half-width by volatility**,
  not a single ±0.5%.

### P1-6 · `maxWin` cap vs. RTP target — capping truncates the tail and lowers measured RTP
**Where.** `spec_v2.md` §2.5 ("cap payout at `maxWin`"), per-game DoD ("max-win cap … enforced") +
every `*_rtp.py` gate.
**What.** High-ceiling games (Limbo to `maxMultiplier`, Dice 49.5×, Keno up to 17,538×) settle some
outcomes at `min(stake×mult, maxWin)`. If `maxWin` binds during an RTP sim, **measured RTP < target**
by construction — yet the gate asserts `RTP ≈ target`. The interaction is never reconciled.
**Why it matters.** A correctly-capped game can fail its own RTP gate, or a worker quietly raises
`maxWin`/loosens tolerance to pass.
**Fix.** State in the `rtp-harness` skill + each tuned-table step: RTP sims set
`(stake, maxWin, maxMultiplier)` so **the cap does not bind** (verify the top multiplier × stake ≤
maxWin), so the gate measures the *uncapped* math; cap behavior gets its **own** unit test
(payout == maxWin when it would exceed). Add this to the engine-purity/ledger reviewers' checklist.

### P1-7 · Client has tooling, a reviewer, and deploy steps — but no implementation step
**Where.** S1 scaffolds `client/`; `add-game-ui` skill + `frontend-renderer-reviewer` exist; S41/S42
deploy "the static client (and verifier)"; S40 load test. But **no S-step builds any client view.**
**What.** All 42 steps are backend/engine/test/deploy. The client is "optional, presentation-only,"
which is fine — but then S41 ("deploy the static client"), S42 ("60-second demo GIF"), and the load
test implicitly assume a client exists, and the kit ships a whole client review apparatus with
nothing scheduled to review.
**Why it matters.** Ambiguous ownership: either the demo has no UI (so S41/S42's client lines are
dead), or a worker improvises an unscoped client late. Both are avoidable.
**Fix.** Choose one and state it: (a) add explicit client step(s) (e.g. `S29.5`/a P7 step: "thin Crash
+ Dice views via `add-game-ui`") if the deployed demo needs a UI; or (b) declare the client **out of
the S1–S42 critical path** and make S41/S42's client/demo references **conditional** ("if a client
was built"). Keep the `add-game-ui` skill + frontend reviewer either way (they're for ad-hoc views).

---

## P2 — Consistency / polish

### P2-1 · Three phase-numbering schemes, no cross-map
`build-plan_v2` uses **M0–M7**; `plan.md`/`steps.md`/`CLAUDE.md` use **P1–P7**; `spec_v2` §6 uses
**Phase 0–6**. They don't line up 1:1 (e.g. build-plan M1=Dice, M2=other Originals; P2 lumps Dice +
Originals). *Inside* `plan.md`, §4.5 even calls poker "**P5**" while §6 calls it "**P6**."
**Fix.** Add one small **M ↔ P ↔ S** mapping table (in `plan.md` or `progress.md`) and fix the
plan §4.5 "P5"→"P6" typo. Pick P-numbering as canonical for execution.

### P2-2 · The two CARRY-FORWARDs are enforced inconsistently
`_curve.py` (S10→S18) is a **real DAG edge** (`S10 → S18`), so the orchestrator serializes them. The
evaluator (`S28→S29`) is only a **prose note** — there is **no edge** in the DAG, and S29's "Depends
on: S6" doesn't mention S28. Since both steps write `engine/src/engine/cards/evaluator.py`, running
them unserialized risks a duplicate/clobber (the exact thing the carry-forward forbids).
**Fix.** Either add a soft constraint "if S28 and S29 are both in scope, serialize them (shared file
`evaluator.py`)" to the DAG + `progress.md`, or move the 5-card evaluator into the spine so both
S28/S29 consume it. State that whichever of S28/S29 runs first **creates** the file.

### P2-3 · `mypy` target is inconsistent (`engine app` vs `engine app verifier`)
`CLAUDE.md` "Per commit" says `mypy engine app`; the Commands block, S1 CI, and the orchestrator
worker prompt say `mypy engine app verifier`; several Verify blocks say just `mypy app`.
**Fix.** Standardize on **`mypy engine app verifier`** everywhere (verifier is type-checked code too).

### P2-4 · "One active round per user" + action serialization is restated per stateful game (DRY)
S12 (Mines) and S14 (HiLo) each say "one-active-round-per-user and serialized reveals"; spec §2.7
makes it a cross-game invariant. The shared bet loop (S6) — the natural owner — doesn't mention it.
**Fix.** Hoist the concurrency guard (one active `(user, game)` round + per-round action
serialization) into the S6 shared stateful path; have S12/S14/S25/S27 *use* it, not re-implement it.

### P2-5 · Welcome grant defined in two places
S8 (auth) issues a "welcome grant via `ledger.grant`"; S36 (faucets) also owns "welcome grant
(idempotent per user)." Two owners → double-grant risk / duplicated logic.
**Fix.** Pick one owner (recommend the S36 faucet module, idempotent per user); S8 *calls* it rather
than issuing its own grant. State the idempotency key for the welcome grant.

### P2-6 · Idempotency-key derivation undefined for poker and multi-bet Crash
`idempotencyKey = hash(betId, opType)` (§2.2) assumes one debit + one credit per `betId`. Poker
money ops (buy-in, leave-with-stack, rake — S33) and Crash's "1–2 bets per round" (§A.4) don't map
cleanly to a single `betId`.
**Fix.** Specify keys for these: e.g. Crash uses a distinct `betId` per placed bet (already implied —
make it explicit); poker uses `hash(tableId, handNo, seat, opType)` for buy-in/leave/rake. Add to §2.2.

### P2-7 · Minor
- **`PURCHASE` ledger type** (§2.9 enum) is unreachable in v1 (IAP is out of scope, §0.2). Fine as a
  seam — add a one-word "(seam; unused in PLAY)" note so a worker doesn't wire it.
- **European-roulette game id** isn't namespaced in S26 (Originals roulette is `originals.roulette`,
  file `roulette99.py`); give the wheel a distinct id (e.g. `table.roulette`) to avoid collision.
- **"43/43 checks" provenance.** `plan.md`/Appendix A assert a prior manual verification; it isn't
  reproducible from the repo. The CI RTP gates (S7+) are the *real*, reproducible proof — say so, so
  the claim isn't mistaken for an in-repo artifact.
- **Pre-S1 environment prerequisites** (uv, pnpm, node, docker, Task, lefthook) aren't stated where a
  runner sees them first. Add a one-line "prereqs / run `task doctor`" note to S1 / the orchestrator
  SETUP. (`task doctor` already exists in the blueprint — just reference it before S1.)
- **`stub.coinflip` (S6) vs `originals.coinflip` (S17)** — two coinflips by design (different ids);
  a one-line note prevents "didn't we already build this?" confusion.

---

## What's already strong (so the above is read in proportion)
- Engine purity as a **dependency fact** (stdlib-only `engine` member) + import-linter + grep +
  dedicated reviewer — defense in depth (the P1-1 holes are the exception that proves the design).
- Double-entry + idempotency + reconciliation discipline is consistent across spec, CLAUDE.md, S3,
  and the ledger reviewer (the *missing piece* is only the counterparty account, P0-1).
- RTP-as-CI as the headline artifact, with anti-clamp discipline repeated everywhere it matters.
- Branch-per-step DAG with explicit human gates (S9/S40/S41) and security-grade routing on
  trust-boundary diffs — the right shape for an agent-driven build.
- Math is internally consistent where I spot-checked it (Dice, Pocket Dice 2d6 pmf, Mines
  `C(25,k)/C(25−M,k)·(1−edge)`, HiLo `(14−r)/13` with ties-win, Roulette 0–99 map all give RTP 0.99).

## Suggested order of operations
1. **Before S1:** P0-2 (repo/path), P1-1 (purity gates), P1-3 (reviewer routing), P2-3/P2-1 (mypy +
   phase map) — all cheap doc edits that make the very first runs correct.
2. **Before S2/S3:** P0-1 (house account) and P0-3 (rounding rule) and P0-4 (config/limit seeding) —
   these shape the schema and money math; fixing them after S3 means a migration + ledger rewrite.
3. **Before each phase:** P1-2 (deploy DAG) before P7; P1-4 (Crash fairness) before S18; P1-5/P1-6
   (RTP cost + cap) before the first heavy RTP gate (S7/S9); P1-7 (client) before S41.
4. **Opportunistic:** the P2 items as you touch those docs/steps.

---
*Review 2026-06-27 · propose-only; no kit doc edited · grounded in `git`/`ls`/`grep` against the live
workspace this session (repo `casino-lacta`, branch `master`, 0 commits, greenfield code).*
