# Casino client & games — full Vite+React+PixiJS client, deployable on Cloudflare Pages

**Status: READY TO EXECUTE (2026-06-28).** Builds the `client/` thin web renderer from an empty
placeholder into a complete, visually polished play-money casino front end for **all 16 games
except multiplayer PvP poker**, talking to the existing FastAPI backend through one generated
contract seam, with a **mock-transport fallback** so the static Cloudflare Pages build is fully
playable as a demo. The single most important boundary: **the client renders server-authoritative
state and NEVER decides an outcome or a balance** — the backend remains the source of truth.

> Relates to: the original build kit `docs/2026-06-27_casino-games_*` (plan/steps/orchestrator/
> progress) which built the backend S1–S42 and treated the client as optional/out-of-critical-path.
> This is the dedicated client follow-on. Specs to read: `docs/2026-06-27_casino-games_spec_v2.md`
> (§2.2 bet object, §2.3 fairness, §2.10 API envelope, Appendix A per-game math),
> `docs/2026-06-27_casino-games_build-plan_v2.md` (§2 trust boundary, §3 shared contracts),
> `CLAUDE.md` (iron rules), `.claude/skills/add-game-ui/SKILL.md` (the per-view recipe),
> `.claude/agents/frontend-renderer-reviewer.md` (the reviewer).

---

## 1. Why, and the honest scope

**End state:** a player opens the deployed site, gets a guest session, and can play all 16 games —
8 instant Originals (Dice, Limbo, Pocket Dice, Mines, Plinko, HiLo, Keno, Roulette 0–99), realtime
Crash, 3 data-driven Slots, and 4 table/video-poker games (Blackjack, Baccarat, European Roulette,
Video Poker) — each with rich PixiJS visuals, a live balance, a provably-fair drawer with a verifier
link, and a persistent "not real money / no prizes" badge. The build is a static SPA that deploys to
Cloudflare Pages and, when `VITE_API_BASE_URL` is set, plays against the real backend; when it is
not, it runs a clearly-labelled in-browser DEMO transport so the Pages preview is self-contained.

**What does NOT change (say it plainly):**
- **No backend/engine/ledger/verifier code changes.** `engine/`, `app/`, `verifier/`, `tests/`,
  `migrations/` are untouched. Engine purity, the double-entry ledger, RNG/fairness, RTP gates, and
  server authority are exactly as built. This is a `client/` + `packages/contracts-ts/` + CI/deploy
  change only.
- **No new REST/WS endpoints.** The client consumes the surface that already exists (see §2). Balance
  comes from `GET /auth/me`; there is no client-built API.
- **No multiplayer PvP poker UI.** `engine/poker/` and the poker WS exist server-side but are out of
  this client's scope (descopable per `CLAUDE.md`). The lobby may show it as "coming soon".
- **`mode=REAL` stays unwired.** Play-money `GOLD` only. No payments, KYC, cash-out, or prizes — the
  client renders the play-money badge persistently and never implies redeemability.
- **No Playwright / browser E2E.** Component tests only (Vitest + React Testing Library), per the
  repo's stated stage.

**Why it's lower-risk than it sounds:** the backend is complete and tested; the contract is fixed and
already camelCased (`BetObject`, `MeResponse`); the repo already ships the reviewer agent
(`frontend-renderer-reviewer`), the per-view skill (`add-game-ui`), the pnpm workspace, biome, and the
CI `ts` paths filter. The genuinely new work is (a) the transport seam + mock adapter, (b) ~16 Pixi
game views, and (c) the Pages deploy wiring. The first game (Dice) is a **gated vertical slice** that
fixes the pattern before the other 15 scale out in parallel.

## 2. Current state (verified against the repo 2026-06-28)

| Piece | Location (file:line) | Status today |
|---|---|---|
| Client app | `client/.gitkeep` (only file) | Empty placeholder; listed in `pnpm-workspace.yaml` |
| Generated contracts | `packages/contracts-ts/.gitkeep` (only file) | Empty placeholder; `openapi-typescript` in root `package.json` devDeps |
| TS workspace root | `package.json`, `pnpm-workspace.yaml`, `biome.json` | pnpm@11.7.0; biome + lefthook + openapi-typescript wired; `lint`/`format` scripts only |
| Task entry points | `Taskfile.yml` | `test` notes "vitest wired when client lands"; `contracts` is a stub; `dev` runs API only |
| CI | `.github/workflows/ci.yml` | `changes` filter has a `ts` output (client/**, packages/**, package.json, pnpm-workspace.yaml, biome.json) but **no `typescript` job exists** — only `python` + `rtp-gate` |
| Auth API | `app/src/app/auth/router.py:64,84,93` | `POST /auth/guest` → `{userId,walletId,currency,mode,tokens{accessToken,refreshToken}}`; `POST /auth/refresh`; `GET /auth/me` → `{userId,walletId,currency,mode,balanceMinor}` |
| Guest funding | `app/src/app/auth/service.py:57` | Guest creation issues a single-owner `welcome_grant` → guests start funded (no faucet needed to play) |
| Bet object | `app/src/app/games/bet_loop.py:115` | camelCase: `betId,gameId,userId,walletId,currency,mode,stakeMinor,status,fairness{serverSeedHash,clientSeed,nonce},input,outcome,createdAt,settledAt,idempotencyKeys` |
| Generic game API | `app/src/app/api/games.py:83,119,149,186` | `POST /games/{id}/bet` → `BetObject`; `POST /games/{id}/spin` → `BetObject` (slots); `POST /games/{id}/action`; `GET /games/{id}/state` |
| Fairness API | `app/src/app/api/fairness.py` | `GET /fairness/{betId}` → seeds/nonce/derivation + verifier link |
| Crash realtime | `app/src/app/ws/crash.py`, `main.py:17` | WS `/games/originals.crash` emits `{type:round, phase:WAITING|RUNNING|CRASHED}`, `tick`; accepts `cashout`; resume via `GET /games/originals.crash/state` |
| Economy / RG | `app/src/app/economy/`, `app/src/app/rg/gate.py` | **NOT REST-exposed** — invoked inside the bet loop; RG block reasons surface as typed bet-rejection errors |
| Registered game IDs | tests + `engine/src/engine/registry.py` | `originals.{dice,limbo,pocketdice,mines,plinko,hilo,keno,roulette,crash}`, `slots.{machine01,machine02,machine03}`, `table.{blackjack,baccarat,roulette,video_poker}` |
| Reviewer + skill | `.claude/agents/frontend-renderer-reviewer.md`, `.claude/skills/add-game-ui/SKILL.md` | Both present; reviewer is read-only and checks the thin-renderer trust boundary + Vitest/RTL adequacy |

**Invariants the change must preserve (wire-level):** the camelCase JSON envelope above is the
contract; the client consumes it via generated types and never reshapes it. Outcomes/balances are
read from server responses (`BetObject.outcome`, `MeResponse.balanceMinor`) — never computed locally.
All money is integer minor units on the wire; formatting is display-only.

## 3. Target architecture

```
BEFORE                                   AFTER
──────                                   ─────
client/.gitkeep  (empty)                 client/  Vite+React+TS SPA
packages/contracts-ts/.gitkeep (empty)   ├─ src/lib/transport/GameClient.ts   ← the seam (interface)
                                         │   ├─ HttpGameClient.ts  (REST+WS, real backend)
[16 games reachable only via raw         │   └─ mock/MockGameClient.ts (quarantined DEMO transport)
 REST/WS — no UI]                        ├─ src/lib/money.ts  (formatMinor / minor-unit input)
                                         ├─ src/components/  (shell, BetControls, ResultPanel,
                                         │     BalanceBadge, PlayMoneyBadge, FairnessDrawer, Lobby)
                                         ├─ src/pixi/  (PixiStage harness, reduced-motion guard)
                                         ├─ src/games/<id>/  (one Pixi view per game)
                                         └─ src/contracts (re-export generated types)
                                         packages/contracts-ts/  ← generated from API OpenAPI
                                         Cloudflare Pages  ← static build of client/dist
```

What the new shape buys: a single **transport seam** (`GameClient`) means "real API vs mock demo" is a
**config/adapter swap, not a rewrite** (the seam philosophy); the renderer depends only on the
interface and never knows which adapter is live. What it costs: a maintained mock adapter and a
generated-types regen step (`task contracts`) whenever the backend OpenAPI changes.

## 4. Key design decisions (the real forks — decided 2026-06-28)

### 4.1 Transport — one `GameClient` seam, two adapters, selected by config
**Decision:** define a single `GameClient` TypeScript interface (auth/session, `bet`, `spin`, `action`,
`state`, `fairness`, `crashSocket`). Ship two implementations behind it: `HttpGameClient` (real
REST+WS using generated contract types) and `MockGameClient`. A factory picks the adapter at runtime:
`VITE_API_BASE_URL` present → Http; absent → Mock. Why: the user chose "API + mock fallback", and a
seam makes that a config flip rather than a fork in every component. Trade accepted: the mock must be
kept roughly in sync with the contract. Consequences: every game view depends ONLY on `GameClient`;
no `fetch`/`WebSocket` in components; the factory is the one place that knows the base URL.

### 4.2 The mock adapter is a quarantined DEMO transport, not a renderer escape hatch
**Decision:** `MockGameClient` lives ONLY under `client/src/lib/transport/mock/`, is the SOLE place in
the client permitted to use `Math.random`, fabricates outcomes for demo play, renders a **persistent
"DEMO — outcomes are mocked, not server-verified" banner** whenever active, and returns
`fairness → unavailable in demo` (it cannot produce a real provably-fair proof). Why: the iron rule is
"the server decides every outcome." A static Pages demo has no server, so a mock necessarily fabricates
results — quarantining it preserves the rule **where it is load-bearing (the renderer)**: components
still never decide anything; they consume `GameClient`. Trade accepted: demo outcomes are not provably
fair (and say so). Consequence: a mechanical grep gate forbids `Math.random`/`crypto.getRandomValues`
under `client/src/{games,components,pixi}` — only `lib/transport/mock/` is exempt.

### 4.3 Contracts — consume generated types, never hand-roll
**Decision:** generate `packages/contracts-ts` from the FastAPI OpenAPI schema via `openapi-typescript`
(wired as `task contracts`), and have the client import those types. Why: one contract, two languages
(`CLAUDE.md`); hand-rolled shapes drift from the server. Trade accepted: a regen step. Consequence:
touching the contract = rerun `task contracts` and commit the generated file in the same commit.

### 4.4 Rendering — rich PixiJS for game visuals, React for shell/forms
**Decision:** React + a CSS design system for the shell, lobby, forms, and result panels; **PixiJS**
for the in-game canvas visuals (Crash curve, Plinko drop, slot reels, card deals, wheels, dice). A
reusable `<PixiStage>` wrapper owns the Pixi lifecycle and a `prefers-reduced-motion` guard. Why: the
user chose the "Rich PixiJS" bar. Trade accepted: more steps/effort and a WebGL test seam (Pixi is
stubbed under jsdom so component tests assert the React contract, not pixels). Consequence: every
animation is **cosmetic and must resolve to the server result** — the Plinko ball lands on the
server-decided slot, the Crash curve is cosmetic while `C` is server truth, reels stop on the server
grid.

### 4.5 Balance & faucet — read `/auth/me`; no top-up endpoint (not a blocker)
**Decision:** the client reads balance from `GET /auth/me` and refreshes it after each settle (the
`BetObject` carries no balance field). Guests are funded by the server's welcome grant, so play works
with zero backend changes. A "get more coins" faucet and RG-limit status are **not REST-exposed**; the
client surfaces RG/limit rejections from the typed bet-error envelope, and "reset session" (new guest)
is the demo's top-up. Why: keeps this a pure client change. Trade accepted: no in-app top-up button v1.
Consequence: documented as a future backend bridge (small `app/` REST add) — explicitly **out of scope
here**.

### 4.6 Vertical-slice gate before scale-out
**Decision:** build **Dice** end-to-end first (S9), then STOP at a human review gate (S10) before the
other 15 games. Why: the cheapest moment to correct the transport seam, the `BetControls` shape, the
`PixiStage` boundary, and the test pattern is after exactly one game proves them. Consequence: S11–S24
copy a validated pattern and run largely in parallel.

## 5. Protocol / interface surface (consumed, not changed)

The client consumes this existing surface through `GameClient`; **nothing here is added or modified
server-side.**

- **`POST /auth/guest`** → bootstrap a funded guest session (stores tokens in memory; refresh via
  `POST /auth/refresh`). **`GET /auth/me`** → `{currency, mode, balanceMinor}` (the balance source).
- **`POST /games/{id}/bet`** (instant Originals + Baccarat + table Roulette), **`/spin`** (slots),
  **`/action`** (stateful: Mines, HiLo, Blackjack, Video Poker), **`GET /games/{id}/state`** (resume) →
  all return/track the `BetObject` envelope (§2).
- **`GET /fairness/{betId}`** → seeds/nonce/derivation + verifier link (the FairnessDrawer).
- **WS `/games/originals.crash`** → `round`(phase)/`tick` events; client sends `cashout` intent;
  **`GET /games/originals.crash/state`** for resume/reconnect. The cashout multiplier is **server-
  stamped** — the client button sends intent only.
- **Auth header:** `Authorization: Bearer <accessToken>` on all game/fairness/me calls; refresh on 401.
- **RG:** a blocked bet returns a typed reason in the error envelope → surfaced in the UI; the
  "not real money · no prizes" badge is always visible (static).

The **mock adapter mirrors these shapes** so the demo is functional, with the §4.2 caveats.

## 6. Phased plan (P1 … P7)

Ordering invariant: build + validate the **vertical slice** before scaling; gate the human review (S10)
and the paid deploy (S28); nothing destructive (there is nothing to decommission — this is purely
additive). Each game ships independently; a half-built lobby still builds and passes.

### P1 — Foundations + vertical slice (S1–S10) — gated
Scaffold, contract seam, transport seam + mock, money utils, app shell + lobby + auth/balance,
PixiStage harness, fairness drawer, shared bet controls, then the **Dice** slice. **Acceptance:** `pnpm
--filter client {tsc --noEmit, vitest run, build}` green; Dice plays against the mock with a Pixi roll
that resolves to the server outcome; fairness drawer + play-money badge render; thin-renderer grep
clean. **S10 = human review gate.**

### P2 — Instant Originals (S11–S14) — parallel after S10
Limbo, Pocket Dice, Keno, Roulette 0–99. **Acceptance:** each renders its bet fixture verbatim, formats
minor units, surfaces fairness; tests green.

### P3 — Stateful / animated Originals (S15–S17) — parallel after S10
Mines (grid, `/action`+`/state`), HiLo (`/action`+`/state`), Plinko (animated drop landing on the
server slot). **Acceptance:** stateful resume works from a `state` fixture; the animation resolves to
the server result; tests green.

### P4 — Crash realtime (S18)
WS view: Pixi multiplier curve + phase, place-bet during WAITING (+ autocashout), server-stamped manual
cashout during RUNNING, reconnect/resume. **Acceptance:** renders an event-fixture sequence; a reconnect
test passes; no client-time cashout decision.

### P5 — Slots (S19–S20)
Data-driven reel framework (Pixi reels, payline highlight) + the 3 machines as presentation manifests.
**Acceptance:** reels stop on the server grid from a spin fixture; each machine renders; tests green.

### P6 — Table & video poker (S21–S24) — parallel after S10
Blackjack, Baccarat, European Roulette, Video Poker. **Acceptance:** card/wheel visuals resolve to the
server result; stateful games resume from a `state` fixture; tests green.

### P7 — Polish, CI, ship (S25–S29)
A11y/reduced-motion/responsive pass; CI `typescript` job + `task test`/`contracts` wired; Cloudflare
Pages config (offline-prepared); **S28 = paid/live deploy (human-authorized, LAST)**; README + demo GIF
+ docs index. **Acceptance:** CI green on a `ts`-only change; `vite build` → `client/dist`; live URL
loads and a real play-money bet works (or demo loads with banner); verifier link resolves.

## 7. Decommission / cleanup checklist

**None — this change is purely additive.** It deletes nothing in `engine/app/verifier/tests/
migrations`. The only "removals" are replacing the two `.gitkeep` placeholders (`client/.gitkeep`,
`packages/contracts-ts/.gitkeep`) with real content, and turning the `Taskfile` `contracts`/`test`
stubs into real commands (S20-era + S26). Rollback at any point is "leave the Pages project
undeployed and don't merge the branch."

## 8. Rule & doc updates (keep docs truthful)

- `Taskfile.yml`: `contracts` stub → real `openapi-typescript` invocation (S2/S26); `test` → also run
  vitest (S26); `dev` may gain a `vite` process (optional, S5).
- `.github/workflows/ci.yml`: add the `typescript` job under the existing `ts` filter (S26).
- `CLAUDE.md` Map/Status: note the client is now built (S29), in the same commit as the work.
- `README.md`: add the client run/deploy section + demo GIF + live URL (S29).
- Each game view lands with its own test in the same commit (TDD).

## 9. Privacy / security / compatibility impact

- **Trust boundary unchanged & enforced:** the client stays presentation-only; the
  `frontend-renderer-reviewer` + the grep gate (§4.2) mechanically forbid client-side
  outcome/RNG/balance logic outside the quarantined mock.
- **Secrets:** none in source. `VITE_API_BASE_URL` is a public build-time config (not a secret). Pages
  deploy credentials are platform-managed and human-handled at S28.
- **CORS:** the real backend must allow the Pages origin — a backend config item flagged for the S28
  human step (not a client change).
- **Compatibility:** additive only; the contract is consumed read-only via generated types.
- **Play-money:** the persistent badge + the demo banner prevent any implication of real money or
  provable fairness in demo mode.

## 10. Risks & rollback

| Risk | Mitigation / rollback |
|---|---|
| Mock adapter drifts from the contract | Generated types (S2) typecheck both adapters against one schema; regen on contract change |
| A game view sneaks outcome logic in to pass a test | Grep gate (§4.2) in CI + `frontend-renderer-reviewer` on every `client/` diff; TDD from server fixtures |
| Pixi/WebGL breaks jsdom tests | `<PixiStage>` stubs Pixi under test; component tests assert the React/data contract, not pixels |
| Cloudflare Pages can't host the API/WS | Architecture already accounts for it: Pages hosts the static SPA; real play points at a separately-hosted API via `VITE_API_BASE_URL`; demo mode needs no backend |
| WS reconnect storms / stale Crash state | Backoff + resume via `/games/originals.crash/state`; reconnect test in S18 |
| Scope creep across 16 views | The S10 gate freezes the pattern; views are thin and parallel; lobby tolerates missing games |
| Paid deploy run unattended | S28 is human-authorized and LAST; never in an auto loop |

## 11. Acceptance criteria (whole change)

- All 16 in-scope games are playable end-to-end against the mock adapter, and against the real backend
  when `VITE_API_BASE_URL` is set, rendering server-authoritative outcomes/balances only.
- Every view: formats minor units at display, surfaces the provably-fair drawer (or "demo" notice), and
  shows the persistent play-money badge.
- `pnpm --filter client exec tsc --noEmit`, `pnpm exec biome check`, `pnpm --filter client exec vitest
  run`, and `pnpm --filter client build` are all green; the thin-renderer grep gate returns nothing.
- CI `typescript` job passes on a `ts`-only PR; `task contracts` regenerates types deterministically.
- `client/dist` deploys to Cloudflare Pages; the live URL loads; a real play-money bet succeeds against
  the configured API (or the demo loads with its banner) and the verifier link resolves.
- No change to `engine/app/verifier/tests/migrations`; backend CI stays green.

## 12. Implementation map

- **Workspace/build:** `client/` (Vite+React+TS, Pixi), `packages/contracts-ts/` (generated),
  `pnpm-workspace.yaml`, `biome.json`, `Taskfile.yml`, `.github/workflows/ci.yml`.
- **Transport seam:** `client/src/lib/transport/{GameClient.ts,HttpGameClient.ts,mock/MockGameClient.ts,index.ts}`.
- **Shared UI:** `client/src/components/{AppShell,Lobby,BalanceBadge,PlayMoneyBadge,FairnessDrawer,BetControls,ResultPanel}.tsx`,
  `client/src/lib/money.ts`, `client/src/pixi/PixiStage.tsx`.
- **Game views:** `client/src/games/<id>/` for each of the 16 IDs in §2.
- **Deploy:** `client/wrangler.toml` (or Pages dashboard config), `client/public/_redirects`,
  `client/public/_headers`.

Sources (verified in-repo 2026-06-28): `app/src/app/auth/router.py`, `app/src/app/auth/service.py`,
`app/src/app/games/bet_loop.py`, `app/src/app/api/games.py`, `app/src/app/api/fairness.py`,
`app/src/app/ws/crash.py`, `app/src/app/main.py`, `engine/src/engine/registry.py`, `Taskfile.yml`,
`package.json`, `pnpm-workspace.yaml`, `.github/workflows/ci.yml`,
`.claude/agents/frontend-renderer-reviewer.md`, `.claude/skills/add-game-ui/SKILL.md`,
`docs/2026-06-27_casino-games_{spec_v2,build-plan_v2}.md`.
