# Casino client & games — Step-by-Step Implementation

**Status: READY TO EXECUTE (2026-06-28).** Companion to `2026-06-28_casino-client_plan.md` (the
*why*); this is the *how* — ordered, **standalone, independently verifiable** changes, each a
paste-ready worker prompt with an explicit Verify block. Builds the `client/` Vite+React+PixiJS
front end for all 16 games except PvP poker, behind one `GameClient` transport seam, deployable to
Cloudflare Pages.

## How to use this

Run steps in dependency order (graph below). Paste a step's fenced **prompt** into a fresh worker
subagent, then run its **Verify** block before proceeding. Each step lands, verifies, and commits on
its own branch `casino-client/S<N>` off `casino-client/main`, in its **own dedicated git worktree**
(`.worktrees/cc-S<N>` or sibling `../.casino-wt/cc-S<N>`) — **never the shared top-level tree**, which
holds in-flight backend work (`casino-games/*`). The change is purely additive — nothing is
destructive — so rollback is "don't merge the branch." Two human gates: **S10** (review gate, after
the Dice slice) and **S28** (paid Cloudflare Pages deploy, LAST).

## Decisions baked in (final, 2026-06-28 — from plan §4)

- **One `GameClient` seam, two adapters** (`HttpGameClient` real REST+WS, `MockGameClient` demo),
  picked by a factory on `VITE_API_BASE_URL` presence. Components depend ONLY on `GameClient` — no
  `fetch`/`WebSocket` in views. (§4.1)
- **The mock is a quarantined DEMO transport** under `client/src/lib/transport/mock/` — the only place
  allowed `Math.random`; renders a persistent "DEMO — mocked, not server-verified" banner; returns
  `fairness → unavailable in demo`. (§4.2)
- **Consume generated `packages/contracts-ts` types** from the API OpenAPI — never hand-roll shapes;
  regen via `task contracts` and commit generated output in the same commit. (§4.3)
- **Rich PixiJS for game canvas, React for shell/forms**; a shared `<PixiStage>` owns the lifecycle +
  `prefers-reduced-motion` guard; **every animation is cosmetic and resolves to the server result**.
  (§4.4)
- **Balance from `GET /auth/me`**, refreshed after each settle; guests are server-funded; no top-up
  endpoint v1; RG/limit rejections surface from the typed bet-error envelope. (§4.5)
- **Dice is a gated vertical slice (S9 → S10 gate)** before the other 15 games scale out. (§4.6)
- **Scope:** all games EXCEPT `originals.*` PvP poker (none in scope) and the multiplayer poker WS —
  the lobby may show poker as "coming soon". `mode=REAL` stays unwired; play-money `GOLD` only.

## Project rules every prompt must respect (stated once)

1. **Thin renderer / server-authoritative (load-bearing).** No outcome/RNG/payout/RTP/balance logic in
   the client outside the quarantined mock. Outcomes, multipliers, payouts, win/loss, and balances come
   from a server response (or `GameClient`) and are merely displayed. Mechanical gate:
   `git grep -nE "Math\.random|crypto\.getRandomValues" -- 'client/src/games' 'client/src/components' 'client/src/pixi'`
   returns nothing.
2. **Money = integer minor units; format only at display** via `formatMinor`; parse/validate stake
   inputs as integer minor units and reject float entry; never do float math on money.
3. **Contracts seam:** import generated `@casino/contracts` types; never hand-roll API shapes; rerun
   `task contracts` and commit the generated file when the contract changes.
4. **TDD, component tests only.** Write the Vitest + React Testing Library test from a server-response
   FIXTURE first, watch it FAIL (component missing), then build the simplest view that passes. No
   outcome logic added to go green. **No Playwright/E2E.**
5. **Animations are cosmetic and MUST resolve to the server result** (Plinko lands on the server slot;
   Crash curve is cosmetic, `C` is server truth; reels stop on the server grid).
6. **Provably-fair + RG surfaced:** per-bet FairnessDrawer with a verifier link (or "unavailable in
   demo"); persistent "Not real money · No prizes" badge; surface server RG block reasons.
7. **Per commit:** `pnpm exec biome check` + `pnpm --filter client exec tsc --noEmit` clean; vitest
   green; stage files BY NAME; Conventional Commits; English; don't bypass hooks; never force-push;
   update the doc describing a behavior in the same commit.
8. **Read first, don't guess:** for any game, read the engine module (`engine/src/engine/...`), the
   spec Appendix A § for that game, and capture a REAL `BetObject`/state fixture (from a backend test
   or the mock) before writing the view. For React/Vite/Pixi/Vitest/RTL API specifics use Context7
   (`resolve-library-id` → `query-docs`) — don't guess framework syntax.
9. **Dedicated worktree per step (MANDATORY).** Every step is implemented in its own git worktree off
   `casino-client/main` (`.worktrees/cc-S<N>`), never the shared top-level checkout — that checkout
   carries uncommitted backend work and editing/committing there can capture it into a client commit.
   (Sandbox/mount note: if `unlink` is blocked, rename stale `.git/**/*.lock` aside before each git op
   and keep operations single-purpose.)

## Dependency graph (quick view)

```
P1 Foundations + slice (gated)
  S1 → {S2, S3, S6}
  {S2,S3} → S4 → S5
  {S4,S5} → S7
  {S5,S3} → S8
  {S6,S7,S8} → S9 (Dice slice) → S10 (HUMAN REVIEW GATE)

P2 Instant Originals (parallel after S10)     : {S11, S12, S13, S14}
P3 Stateful/animated Originals (after S10)     : {S15, S16, S17}
P4 Crash realtime (after S10,S6)               : S18
P5 Slots (after S10,S6)                         : S19 → S20
P6 Table & video poker (after S10,S6)           : {S21, S22, S23, S24}
   (S11..S24 each add ONE line to client/src/games/registry.tsx — trivial merge; rebase later branch)

P7 Polish & ship
  {all game steps S9,S11..S24} → S25 (a11y/reduced-motion)
  S9 → S26 (CI typescript job)            [land after the suite exists]
  {S25,S26} → S27 (Pages config, offline)
  {S27 + all} → S28 (DEPLOY — PAID, human go, LAST)
  S27 → S29 (README/docs; live URL + GIF filled post-S28)
```

**Parallel sets:** `{S2,S3,S6}` · `{S11,S12,S13,S14}` · `{S15,S16,S17}` · `{S21,S22,S23,S24}` (S18 and
S19 may also run alongside these). Steps S11–S24 each append one lazy-import line to
`client/src/games/registry.tsx`; treat that one file as a serialize point (rebase the later branch).

---

# Phase P1 — Foundations + vertical slice (gated)

### S1 — Scaffold the Vite + React + TS client in the pnpm workspace
**Goal:** `client/` is a real Vite+React+TS app wired into the workspace; install, typecheck, lint,
a smoke test, and a production build all pass.
**Depends on:** none.

```
Scaffold a Vite + React + TypeScript app in client/ (the package is already listed in
pnpm-workspace.yaml; replace client/.gitkeep). Read CLAUDE.md, package.json, pnpm-workspace.yaml,
biome.json, Taskfile.yml first. Requirements:
- client/package.json: name "@casino/client", private, type module; deps react + react-dom + pixi.js
  + react-router-dom; devDeps vite, @vitejs/plugin-react, typescript, vitest, @testing-library/react,
  @testing-library/jest-dom, jsdom. Scripts: "dev","build" (vite build),"preview","test" (vitest run),
  "typecheck" (tsc --noEmit). Pin versions; check current ones via Context7 if unsure.
- client/tsconfig.json (strict), client/vite.config.ts (react plugin; base "./" for Pages-relative
  assets; vitest config: environment jsdom, globals true, setup file importing @testing-library/jest-dom).
- client/index.html, client/src/main.tsx (mount <App/>), client/src/App.tsx (a minimal placeholder),
  client/src/setupTests.ts.
- One smoke test client/src/App.test.tsx that renders <App/> and asserts a visible heading (RED first:
  write it before App renders the heading, confirm it fails, then make it pass).
- Ensure biome.json covers client/ (TS/TSX). Do NOT add Playwright. Do NOT touch engine/app/verifier.
Project rules: thin renderer (no outcome logic — placeholder only); biome + tsc clean before commit;
Conventional Commits; stage by name. Run the Verify block and paste ACTUAL output.
```

**Verify:**
- `pnpm install` completes (workspace resolves `@casino/client`).
- `pnpm --filter @casino/client exec tsc --noEmit` → clean.
- `pnpm exec biome check client` → clean.
- `pnpm --filter @casino/client test` → the smoke test passes.
- `pnpm --filter @casino/client build` → `client/dist/index.html` exists.
- Diff touches only `client/**` (+ `biome.json`/`pnpm-lock.yaml` if needed).

---

### S2 — Generate the TypeScript contracts from the API OpenAPI schema
**Goal:** `packages/contracts-ts` holds generated types from the live FastAPI OpenAPI, consumed by the
client; `task contracts` regenerates them deterministically.
**Depends on:** S1.

```
Wire the OpenAPI→TS contract seam (replace packages/contracts-ts/.gitkeep). Read CLAUDE.md (contracts
seam), Taskfile.yml (the `contracts` stub), root package.json (openapi-typescript devDep), and
app/src/app/main.py / app/src/app/games/bet_loop.py (the BetObject envelope).
- Add a script that dumps the API schema WITHOUT a DB or running server, e.g.
  `uv run python -c "import json,app.main as m; print(json.dumps(m.app.openapi()))"` (run with
  `--app-dir app/src`) into packages/contracts-ts/openapi.json, then run
  `pnpm exec openapi-typescript packages/contracts-ts/openapi.json -o packages/contracts-ts/src/schema.ts`.
- packages/contracts-ts/package.json: name "@casino/contracts", exports the generated types (re-export
  from src/index.ts). Make `@casino/client` depend on `@casino/contracts` (workspace:*).
- Replace the Taskfile `contracts` stub with the real dump+generate commands.
- Prove consumption: add client/src/contracts/index.ts re-exporting a couple of types (e.g. the bet
  object + MeResponse) and reference one in a trivial typed const so tsc fails if the type is missing.
Rules: never hand-roll shapes; regenerate + commit generated output together; do not edit engine/app.
Run Verify and paste ACTUAL output.
```

**Verify:**
- `task contracts` regenerates `packages/contracts-ts/src/schema.ts` (non-empty; contains the bet
  object + `/auth/me` shapes).
- `pnpm install` links `@casino/contracts` into the client.
- `pnpm --filter @casino/client exec tsc --noEmit` → clean (consuming a generated type).
- Re-running `task contracts` produces no diff (deterministic).

---

### S3 — Money + minor-units utilities
**Goal:** a single `formatMinor` display formatter and an integer-minor stake parser/validator, fully
unit-tested; no float math on money anywhere.
**Depends on:** S1. (Parallel with S2, S6.)

```
Add client/src/lib/money.ts: formatMinor(amountMinor: number, currency: string): string (integer minor
units → display string, e.g. 198 GOLD-cents → "1.98"); parseStakeToMinor(input: string): number|Error
(accepts integer minor units / a decimal with the currency's fixed scale, REJECTS NaN/negative/float
overflow / too many decimals); and a currency-scale constant. Read CLAUDE.md (money rule) + spec §2.1.
Write client/src/lib/money.test.ts FIRST (RED): formatting round-trips, rejection of bad input, no
floating-point drift on representative amounts. Then implement. No payout/multiplier math here — this is
formatting + input validation only.
Rules: integer minor units only; reject floats at the boundary; thin renderer. Run Verify, paste output.
```

**Verify:**
- `pnpm --filter @casino/client exec vitest run src/lib/money.test.ts` → green (incl. rejection cases).
- `pnpm --filter @casino/client exec tsc --noEmit` → clean.
- `git grep -nE "parseFloat|Number\(.*\)\s*[*/]" -- client/src/lib/money.ts` → no float math on money.

---

### S4 — Transport seam: `GameClient` interface + factory + Http & Mock adapters
**Goal:** one `GameClient` interface with a real `HttpGameClient` (REST+WS using generated types) and a
quarantined `MockGameClient`; a factory selects by `VITE_API_BASE_URL`; both are tested.
**Depends on:** S2, S3.

```
Build the transport seam in client/src/lib/transport/. Read plan §4.1–4.2, §5, CLAUDE.md (seams +
trust boundary), and the contract types from @casino/contracts.
- GameClient.ts: the interface — startGuestSession(), me(), bet(gameId,input), spin(gameId,input),
  action(gameId,actionInput), state(gameId), fairness(betId), crashSocket(handlers) (returns a
  subscription with close()), and an `isDemo: boolean`. Use generated types for all payloads.
- HttpGameClient.ts: implements it via fetch (Bearer token mgmt: store tokens in memory, refresh on
  401) + a WebSocket for crashSocket; base URL from VITE_API_BASE_URL. NO outcome logic — it only
  transports.
- mock/MockGameClient.ts (the ONLY file allowed Math.random): fabricates plausible BetObject/state/
  crash-event sequences for demo play; isDemo=true; fairness() returns an "unavailable in demo" marker.
  Keep it shaped to the contract types.
- index.ts: createGameClient() → if import.meta.env.VITE_API_BASE_URL is set, HttpGameClient, else
  MockGameClient. Export a React context/provider + useGameClient() hook.
Tests FIRST (RED): mock adapter returns contract-shaped objects; the factory picks Mock when the env is
unset and Http when set (stub import.meta.env); HttpGameClient.bet() calls the right URL/method/headers
with fetch mocked (vi.fn) and returns the parsed body unchanged. No real network in tests.
Rules: components will depend ONLY on this seam; quarantine all randomness in mock/; thin renderer;
generated types only. Run Verify, paste output.
```

**Verify:**
- `pnpm --filter @casino/client exec vitest run src/lib/transport` → green (factory + both adapters).
- `pnpm --filter @casino/client exec tsc --noEmit` → clean.
- `git grep -nE "Math\.random|crypto\.getRandomValues" -- client/src/lib/transport` → matches ONLY in
  `client/src/lib/transport/mock/`.

---

### S5 — App shell, lobby, auth/session bootstrap, balance + play-money badge
**Goal:** an app shell with routing, a lobby of all 16 game cards, a guest-session bootstrap, a live
`BalanceBadge` (from `/auth/me`), the persistent play-money badge, and the DEMO banner when the mock is
active.
**Depends on:** S4.

```
Build the shell in client/src/components/ + routing. Read plan §3,§5,§6; use the GameClient seam from
S4 and formatMinor from S3.
- AppShell.tsx: top bar with app name, BalanceBadge, a persistent PlayMoneyBadge ("Not real money · No
  prizes"), and — when useGameClient().isDemo — a DemoBanner ("DEMO — outcomes are mocked, not
  server-verified"). React Router: "/" = Lobby, "/play/:gameId" = a GameRoute that lazy-loads the view
  from client/src/games/registry.tsx (create the registry with NO games yet — an empty map + a
  "coming soon" fallback so unknown ids render a placeholder).
- Lobby.tsx: a responsive grid of GameCard for the 16 in-scope ids (originals.dice/limbo/pocketdice/
  mines/plinko/hilo/keno/roulette/crash, slots.machine01..03, table.blackjack/baccarat/roulette/
  video_poker) with display names; clicking routes to /play/:gameId. (Poker may appear disabled as
  "coming soon".)
- session: on load, startGuestSession() then me(); store balanceMinor in app state; expose a
  refreshBalance() to call after settles. BalanceBadge renders formatMinor(balanceMinor,currency).
Tests FIRST (RED) from fixtures: Lobby renders 16 cards; BalanceBadge renders a fixture balance via
formatMinor; the DemoBanner shows when isDemo and the PlayMoneyBadge always shows. Mock GameClient in
tests.
Rules: balance is a server field (never computed); thin renderer; minor units formatted at display.
Run Verify, paste output.
```

**Verify:**
- `pnpm --filter @casino/client exec vitest run src/components` → green (lobby card count, badges,
  banner).
- `pnpm --filter @casino/client exec tsc --noEmit` clean; `pnpm exec biome check client` clean.
- `git grep -nE "Math\.random|crypto\.getRandomValues" -- client/src/components` → nothing.
- `pnpm --filter @casino/client build` → succeeds.

---

### S6 — PixiStage harness + reduced-motion guard
**Goal:** a reusable `<PixiStage>` React wrapper that owns the Pixi app lifecycle (create on mount,
destroy on unmount, resize), respects `prefers-reduced-motion`, and is safely stubbed under jsdom for
tests.
**Depends on:** S1. (Parallel with S2, S3.)

```
Add client/src/pixi/PixiStage.tsx (+ small shared utils). Read CLAUDE.md (thin renderer) and use
Context7 for the current PixiJS v8 Application/lifecycle API — don't guess.
- <PixiStage> mounts a Pixi Application into a container ref, calls an onReady(app) so callers add
  display objects, and tears down cleanly on unmount (no leaked tickers/canvases).
- A useReducedMotion() hook reading prefers-reduced-motion; PixiStage exposes it so views can render a
  static end-state instead of animating when reduced motion is requested.
- Test seam: under jsdom (no WebGL) PixiStage must NOT throw — guard creation so component tests render
  the React wrapper without a real GL context (e.g. a test-mode/no-op renderer). PixiStage holds NO
  game logic — it is a presentation host only.
Tests FIRST (RED): PixiStage mounts and unmounts without throwing under jsdom; useReducedMotion returns
the mocked matchMedia value. Then implement.
Rules: cosmetic presentation only; no outcome/RNG logic; clean teardown. Run Verify, paste output.
```

**Verify:**
- `pnpm --filter @casino/client exec vitest run src/pixi` → green (mount/unmount + reduced-motion).
- `pnpm --filter @casino/client exec tsc --noEmit` → clean.
- `git grep -nE "Math\.random|crypto\.getRandomValues" -- client/src/pixi` → nothing.

---

### S7 — FairnessDrawer + verifier link
**Goal:** a drawer that, given a `betId`, fetches fairness via `GameClient.fairness` and renders
seeds/nonce/derivation + the verifier link; shows "unavailable in demo" in mock mode.
**Depends on:** S4, S5.

```
Add client/src/components/FairnessDrawer.tsx. Read spec §2.3 + the /fairness/{betId} response shape and
the verifier link convention (app/src/app/api/fairness.py). Use the GameClient seam.
- Given a betId, call fairness(betId); render serverSeedHash, clientSeed, nonce, derivation, and an
  outbound link to the verifier for that bet. When the client isDemo (mock), render a clear "Provable
  fairness is unavailable in demo mode" notice instead of fake proof data.
- A small "Verify this bet" affordance that opens the drawer, wired so game views can trigger it with a
  betId.
Tests FIRST (RED) from a fairness fixture: renders the hash/nonce and a correct verifier href; the demo
notice shows when isDemo. Then implement.
Rules: never fabricate fairness data; surface the real server fields only; thin renderer. Run Verify,
paste output.
```

**Verify:**
- `pnpm --filter @casino/client exec vitest run src/components/FairnessDrawer.test.tsx` → green (fields
  + verifier href + demo notice).
- `pnpm --filter @casino/client exec tsc --noEmit` → clean.

---

### S8 — Shared `BetControls` + `ResultPanel`
**Goal:** reusable bet UI — a stake input in minor units (validated, with quick-stake buttons), a Bet
button, and a result panel that renders the server outcome/multiplier/payout formatted via
`formatMinor` — that every instant game composes.
**Depends on:** S5, S3.

```
Add client/src/components/BetControls.tsx and ResultPanel.tsx. Use formatMinor/parseStakeToMinor (S3)
and the GameClient seam (S4).
- BetControls: a stake field validated to integer minor units (reject float/negative; show server
  min/max-bet rejections), quick-stake (½, 2×, max) buttons computed on the integer stake, and a Bet
  button that calls an onBet(stakeMinor) the parent provides (the parent calls GameClient.bet/spin and
  refreshes balance). BetControls itself decides NOTHING about outcome.
- ResultPanel: given a settled BetObject, render status (WON/LOST/CASHED_OUT), outcome.multiplier, and
  outcome.payoutMinor via formatMinor, plus a "Verify" trigger (opens the S7 FairnessDrawer with the
  betId). Renders the server result verbatim.
Tests FIRST (RED) from a BetObject fixture: ResultPanel shows the fixture's multiplier + formatted
payout + status; BetControls rejects a float stake and emits the right integer stakeMinor on Bet. Then
implement.
Rules: minor units in/at display; no payout math (read outcome.payoutMinor); thin renderer;
server-authoritative. Run Verify, paste output.
```

**Verify:**
- `pnpm --filter @casino/client exec vitest run src/components/BetControls.test.tsx src/components/ResultPanel.test.tsx`
  → green.
- `pnpm --filter @casino/client exec tsc --noEmit` clean; `git grep -nE "Math\.random|payoutMinor\s*=" -- client/src/components` shows no local payout computation.

---

### S9 — Dice game view (vertical slice — the pattern every game copies)
**Goal:** a complete `originals.dice` view: BetControls/ResultPanel + a Pixi dice-roll animation that
resolves to the server outcome; plays via `GameClient.bet`; fairness link works; covered by component
tests from a real bet fixture.
**Depends on:** S6, S7, S8.

```
Build client/src/games/dice/ (DiceView.tsx + a Pixi DiceStage + tests) and register it in
client/src/games/registry.tsx. Invoke the .claude/skills/add-game-ui recipe. Read
engine/src/engine/games/dice.py, the spec Appendix A Dice §, and capture a REAL originals.dice
BetObject (from a backend test fixture or the mock) for the test fixture — do NOT invent field names.
- DiceView composes BetControls + ResultPanel; onBet → GameClient.bet("originals.dice", input) → render
  the returned BetObject; refresh balance after settle; wire the FairnessDrawer with the returned betId.
- DiceStage (PixiStage): a cosmetic roll animation that lands on the server-decided roll result; when
  reduced-motion, render the static result immediately. The animation MUST resolve to the server
  outcome — no client-side roll.
Tests FIRST (RED) from the bet fixture: the view renders the server multiplier/payout/roll verbatim;
the fairness link uses the returned betId; a float stake is rejected. Then implement.
Rules: thin renderer; cosmetic animation resolves to the server result; minor units at display;
fairness + play-money surfaced. This view is the reference pattern for S11–S24. Run Verify, paste output.
```

**Verify:**
- `pnpm --filter @casino/client exec vitest run src/games/dice` → green (renders server outcome; fairness
  link; float rejected).
- `pnpm --filter @casino/client exec tsc --noEmit` clean; `pnpm exec biome check client` clean.
- `git grep -nE "Math\.random|crypto\.getRandomValues" -- client/src/games` → nothing.
- `pnpm --filter @casino/client build` → succeeds; Dice is reachable at `/play/originals.dice`.

---

### S10 — REVIEW GATE (HUMAN "go" — no code)
**Goal:** confirm the foundation patterns before scaling to 15 more games.
**Depends on:** S9.

```
STOP. This is a human review gate, not a code change. Present for sign-off: the GameClient seam shape
(S4), the quarantine of randomness to lib/transport/mock/, BetControls/ResultPanel ergonomics (S8), the
PixiStage boundary + reduced-motion (S6), the FairnessDrawer (S7), and the Dice slice (S9) — including
the thin-renderer grep result and the test pattern. Record an explicit go/no-go in
2026-06-28_casino-client_plan.md. Do NOT start S11+ until the reviewer/human approves; fold any
requested pattern changes back into S4–S9 first.
```

**Verify:** human go/no-go recorded in the plan; the `frontend-renderer-reviewer` has reviewed the
S1–S9 surface; the thin-renderer grep is clean. **Do not proceed to P2–P6 without sign-off.**

---

# Phase P2 — Instant Originals (parallel after S10)

> Each step below: read the engine module + spec Appendix A §, capture a REAL BetObject fixture, write
> the RTL test from it FIRST (RED), then build a view that composes BetControls/ResultPanel + a
> cosmetic Pixi stage resolving to the server outcome, and add ONE lazy-import line to
> `client/src/games/registry.tsx`. Same Verify shape as S9 (scoped to the game's folder).

### S11 — Limbo view
**Goal:** `originals.limbo` plays via `GameClient.bet` with a cosmetic rising-multiplier Pixi visual
that resolves to the server multiplier. **Depends on:** S10.

```
Build client/src/games/limbo/ + register it. Invoke add-game-ui. Read engine/src/engine/games/limbo.py
+ spec Appendix A Limbo; capture a real originals.limbo BetObject fixture. The view composes
BetControls/ResultPanel; a Pixi visual climbs to and stops at the server-returned multiplier (static
under reduced-motion). Test FIRST from the fixture: renders the server multiplier/payout, fairness link,
float-stake rejection. Rules: thin renderer; cosmetic animation resolves to server result; minor units;
fairness/play-money surfaced. Run Verify, paste output.
```

**Verify:** `pnpm --filter @casino/client exec vitest run src/games/limbo` green; `tsc --noEmit` clean;
`git grep -nE "Math\.random|crypto\.getRandomValues" -- client/src/games` empty; `build` succeeds.

### S12 — Pocket Dice view
**Goal:** `originals.pocketdice` plays via `GameClient.bet`; cosmetic dice visual resolves to the server
result. **Depends on:** S10.

```
Build client/src/games/pocketdice/ + register it. Invoke add-game-ui. Read
engine/src/engine/games/pocketdice.py + spec Appendix A; capture a real BetObject fixture. Compose
BetControls/ResultPanel + a cosmetic Pixi dice visual landing on the server result. Test FIRST from the
fixture (server outcome verbatim, fairness link, float rejection). Rules as S11. Run Verify, paste output.
```

**Verify:** `vitest run src/games/pocketdice` green; `tsc` clean; games grep empty; build succeeds.

### S13 — Keno view
**Goal:** `originals.keno` pick-grid (choose spots) → `GameClient.bet`; a draw animation reveals the
server-drawn numbers and hits. **Depends on:** S10.

```
Build client/src/games/keno/ + register it. Invoke add-game-ui. Read engine/src/engine/games/keno.py +
spec Appendix A Keno; capture a real BetObject fixture (picks + drawn numbers + multiplier). A grid lets
the player choose spots; onBet sends the picks; a cosmetic Pixi draw reveals the server-drawn numbers
and highlights hits (static under reduced-motion) — the draw is the SERVER's, not generated locally.
Test FIRST from the fixture (drawn numbers + hits + payout from server; float rejection). Rules as S11
(the drawn set comes from the server). Run Verify, paste output.
```

**Verify:** `vitest run src/games/keno` green; `tsc` clean; games grep empty; build succeeds.

### S14 — Roulette 0–99 view (`originals.roulette`)
**Goal:** the Originals 0–99 roulette: number/range bets → `GameClient.bet`; a cosmetic spin lands on
the server pocket. **Depends on:** S10.

```
Build client/src/games/roulette99/ for game id originals.roulette + register it. Invoke add-game-ui.
Read engine/src/engine/games/roulette99.py + spec Appendix A; capture a real BetObject fixture. A bet
surface (number / range / parity as the engine supports) → onBet; a cosmetic Pixi spin lands on the
server-returned pocket (static under reduced-motion). Test FIRST from the fixture (server pocket +
payout; float rejection). Rules as S11. Run Verify, paste output.
```

**Verify:** `vitest run src/games/roulette99` green; `tsc` clean; games grep empty; build succeeds.

---

# Phase P3 — Stateful / animated Originals (parallel after S10)

### S15 — Mines view (stateful)
**Goal:** `originals.mines`: bet to init the grid, reveal tiles via `/action`, cash out; resume via
`/state`; a Pixi grid reveals server-decided tiles. **Depends on:** S10.

```
Build client/src/games/mines/ + register it. Invoke add-game-ui. Read engine/src/engine/games/mines.py
+ spec Appendix A Mines; capture REAL fixtures for the bet (init), an /action reveal, and a /state
resume. Flow: BetControls starts the round (single debit) → a Pixi grid where each pick calls
GameClient.action and renders the server-returned tile (safe/mine) and running multiplier → a Cash Out
button credits via the server. On mount, call GameClient.state to resume an active round. The grid
NEVER knows where mines are — it renders only what /action/state returns.
Tests FIRST from fixtures: a revealed-safe tile + running multiplier render from the action fixture;
resume renders the active grid from the state fixture; cashout shows the server payout. Rules: thin
renderer; single-debit/single-credit is the server's; minor units; fairness/play-money. Run Verify,
paste output.
```

**Verify:** `pnpm --filter @casino/client exec vitest run src/games/mines` green (action + resume +
cashout); `tsc` clean; `git grep -nE "Math\.random|crypto\.getRandomValues" -- client/src/games` empty;
build succeeds.

### S16 — HiLo view (stateful)
**Goal:** `originals.hilo`: guess higher/lower via `/action`, running multiplier, cash out; resume via
`/state`; Pixi card reveal. **Depends on:** S10.

```
Build client/src/games/hilo/ + register it. Invoke add-game-ui. Read engine/src/engine/games/hilo.py +
spec Appendix A; capture REAL bet/action/state fixtures. Flow: start round → Higher/Lower buttons call
GameClient.action and render the server-revealed card + updated multiplier → Cash Out credits via
server; resume via GameClient.state on mount. The next card comes from the SERVER only.
Tests FIRST from fixtures: a guess renders the server card + multiplier; resume restores the run;
cashout shows the server payout. Rules as S15. Run Verify, paste output.
```

**Verify:** `vitest run src/games/hilo` green; `tsc` clean; games grep empty; build succeeds.

### S17 — Plinko view (animated drop)
**Goal:** `originals.plinko`: bet → a Pixi ball drop that **lands on the server-decided slot**;
multiplier/payout from the server. **Depends on:** S10.

```
Build client/src/games/plinko/ + register it. Invoke add-game-ui. Read engine/src/engine/games/plinko.py
+ spec Appendix A Plinko; capture a real BetObject fixture (the server returns the landing slot/path +
multiplier). The Pixi board animates a ball that MUST terminate on the server-returned slot (drive the
animation FROM the server result, never a client-side physics outcome); reduced-motion places the ball
on the slot immediately. Test FIRST from the fixture: the rendered landing slot + multiplier + payout
equal the server's; float rejection. Rules: cosmetic animation resolves to the server result (the
load-bearing check here); thin renderer; minor units; fairness/play-money. Run Verify, paste output.
```

**Verify:** `vitest run src/games/plinko` green (rendered slot == server slot); `tsc` clean; games grep
empty; build succeeds.

---

# Phase P4 — Crash realtime

### S18 — Crash view (WebSocket, realtime)
**Goal:** `originals.crash`: subscribe to the crash WS via `GameClient.crashSocket`; render a Pixi
multiplier curve + phase; place a bet during WAITING (+ optional autocashout); manual cashout during
RUNNING is **server-stamped**; resume/reconnect via `/games/originals.crash/state`.
**Depends on:** S10, S6.

```
Build client/src/games/crash/ + register it. Invoke add-game-ui. Read app/src/app/ws/crash.py for the
event shapes (round/phase WAITING|RUNNING|CRASHED, tick; client→server cashout) and the
GET /games/originals.crash/state resume shape; capture REAL event + state fixtures. Build:
- crashSocket subscription rendering a Pixi multiplier curve that tracks `tick` (cosmetic) and a phase
  banner; the curve is cosmetic — C is the server's truth, revealed at CRASHED.
- During WAITING: BetControls to place a bet (single debit) with an optional autocashout multiplier
  input. During RUNNING: a Cash Out button that SENDS INTENT only — the server stamps the authoritative
  multiplier; render WON/CASHED_OUT or LOST from the server settle. NEVER decide cashout on client time.
- On mount/reconnect: call state to restore the current round + the caller's active bets; reconnect with
  backoff.
Tests FIRST from fixtures: feeding a round→tick→crash event sequence renders phases + the server crash
point; a cashout during RUNNING shows the SERVER-stamped multiplier (not a client value); a reconnect
test restores active bets from the state fixture. Mock the socket in tests.
Rules: server-authoritative cashout; cosmetic curve; WS reconnect hygiene; minor units; fairness (per
round seed) + play-money surfaced. Run Verify, paste output.
```

**Verify:**
- `pnpm --filter @casino/client exec vitest run src/games/crash` → green (event sequence; server-stamped
  cashout; reconnect/resume).
- `pnpm --filter @casino/client exec tsc --noEmit` clean; `git grep -nE "Date\.now\(\).*cashout|Math\.random|crypto\.getRandomValues" -- client/src/games/crash` shows no client-time cashout decision / no RNG.
- `pnpm --filter @casino/client build` → succeeds.

---

# Phase P5 — Slots

### S19 — Slots reel framework (data-driven)
**Goal:** a reusable Pixi `SlotMachine` component that renders a server `spin` result — the grid, the
winning lines, and features — driven by a client-side presentation manifest (symbols/paylines) that is
cosmetic only; the server decides the grid.
**Depends on:** S10, S6.

```
Build client/src/games/slots/ (framework: SlotMachine.tsx + a presentation-manifest type + tests) +
register the route base. Invoke add-game-ui. Read engine/src/engine/slots/framework.py +
engine/src/engine/slots/features.py + spec Appendix A slots; capture a REAL slots spin BetObject fixture
(the server returns the stopped grid + line wins + any feature triggers).
- SlotMachine takes a presentation manifest (symbol art, reel layout, paylines — COSMETIC) and a server
  spin result, and animates reels that STOP on the server grid, then highlights the server's winning
  lines and renders feature outcomes. onSpin → GameClient.spin(machineId, input). Reduced-motion snaps
  to the final grid.
Test FIRST from the spin fixture: reels render the server grid; the highlighted lines + total win +
payout equal the server's. Rules: reels stop on the SERVER grid (load-bearing); manifest is cosmetic;
thin renderer; minor units; fairness/play-money. Run Verify, paste output.
```

**Verify:** `pnpm --filter @casino/client exec vitest run src/games/slots` green (grid + wins == server);
`tsc` clean; `git grep -nE "Math\.random|crypto\.getRandomValues" -- client/src/games` empty; build
succeeds.

### S20 — Wire the 3 slot machines + finalize the `contracts`/`test` Taskfile entries
**Goal:** `slots.machine01/02/03` render via the S19 framework with their own presentation manifests and
routes. **Depends on:** S19.

```
Add presentation manifests + routes for slots.machine01, slots.machine02, slots.machine03 using the S19
SlotMachine framework (read each machine's server config/spec for symbols/paylines — manifests are
cosmetic mirrors, NOT the source of outcomes). Register all three in client/src/games/registry.tsx.
Test FIRST: each machine renders its own spin fixture's grid + wins. Rules as S19. Run Verify, paste
output.
```

**Verify:** `pnpm --filter @casino/client exec vitest run src/games/slots` green for all three machines;
`tsc` clean; games grep empty; all three reachable at `/play/slots.machine0{1,2,3}`; build succeeds.

---

# Phase P6 — Table & video poker (parallel after S10, S6)

### S21 — Blackjack view (stateful)
**Goal:** `table.blackjack`: deal then hit/stand/double/split via `/action`; resume via `/state`; Pixi
card deals resolve to server cards. **Depends on:** S10, S6.

```
Build client/src/games/blackjack/ + register it. Invoke add-game-ui. Read
engine/src/engine/table/blackjack.py + spec Appendix A Blackjack; capture REAL bet/action(hit,stand,
double,split)/state fixtures. Flow: bet → deal (server cards) → action buttons call GameClient.action and
render the server hand/dealer reveal → settle shows the server result; resume via state on mount. Cards
come from the SERVER only. Test FIRST from fixtures: a hit renders the server card + hand total; resume
restores the hand; settle shows server payout. Rules: thin renderer; server-authoritative; minor units;
fairness/play-money. Run Verify, paste output.
```

**Verify:** `pnpm --filter @casino/client exec vitest run src/games/blackjack` green (action + resume +
settle); `tsc` clean; games grep empty; build succeeds.

### S22 — Baccarat view
**Goal:** `table.baccarat`: bet Player/Banker/Tie → `GameClient.bet`; Pixi card reveal resolves to the
server coup. **Depends on:** S10, S6.

```
Build client/src/games/baccarat/ + register it. Invoke add-game-ui. Read
engine/src/engine/table/baccarat.py + spec Appendix A; capture a real BetObject fixture (the server
returns both hands + the result). Bet on Player/Banker/Tie → onBet; a cosmetic Pixi reveal shows the
server's coup (static under reduced-motion). Test FIRST from the fixture (server hands + result + payout;
float rejection). Rules as S21 (the coup is the server's). Run Verify, paste output.
```

**Verify:** `vitest run src/games/baccarat` green; `tsc` clean; games grep empty; build succeeds.

### S23 — European Roulette view (`table.roulette`)
**Goal:** `table.roulette`: a roulette bet grid (inside/outside) → `GameClient.bet`; a cosmetic wheel
spin lands on the server pocket. **Depends on:** S10, S6.

```
Build client/src/games/table_roulette/ for id table.roulette + register it. Invoke add-game-ui. Read
engine/src/engine/table/roulette_wheel.py + spec Appendix A; capture a real BetObject fixture. A standard
bet layout (straight/split/red-black/odd-even/dozens as the engine supports) → onBet; a cosmetic Pixi
wheel lands on the server-returned pocket (static under reduced-motion). Test FIRST from the fixture
(server pocket + payout; float rejection). Rules as S22. Run Verify, paste output.
```

**Verify:** `vitest run src/games/table_roulette` green; `tsc` clean; games grep empty; build succeeds.

### S24 — Video Poker view (stateful)
**Goal:** `table.video_poker`: deal → hold/draw via `/action`; Pixi 5-card hand resolves to server
cards; payout from the server paytable. **Depends on:** S10, S6.

```
Build client/src/games/video_poker/ + register it. Invoke add-game-ui. Read
engine/src/engine/table/video_poker.py + spec Appendix A; capture REAL bet(deal)/action(hold+draw)/state
fixtures. Flow: bet deals 5 server cards → hold toggles → Draw calls GameClient.action and renders the
server's drawn hand + ranked result + payout; resume via state. Cards/rank/payout come from the SERVER.
Test FIRST from fixtures: deal renders server cards; draw renders the server final hand + payout; holds
are sent, not evaluated locally. Rules as S21. Run Verify, paste output.
```

**Verify:** `pnpm --filter @casino/client exec vitest run src/games/video_poker` green (deal + draw +
resume); `tsc` clean; games grep empty; build succeeds.

---

# Phase P7 — Polish & ship

### S25 — Accessibility, reduced-motion, responsive pass
**Goal:** every view is keyboard-navigable, honors `prefers-reduced-motion` (static end-states), and is
usable on mobile widths; the play-money badge + fairness affordance are reachable everywhere.
**Depends on:** S9, S11–S24.

```
Do an a11y/reduced-motion/responsive pass across all built views. Read the design:accessibility-review
guidance if available. For each game: keyboard focus order + visible focus, ARIA labels on bet controls,
color-contrast on result states, and a reduced-motion path that snaps Pixi visuals to the server
end-state (assert it). Ensure the lobby + shell reflow at mobile widths. Add/extend component tests:
each game's reduced-motion path renders the final server result without animation; bet controls are
operable by keyboard. Do NOT add Playwright. Rules: thin renderer; cosmetic-only motion; surface
fairness + play-money. Run Verify, paste output.
```

**Verify:**
- `pnpm --filter @casino/client exec vitest run` → full suite green incl. new reduced-motion/keyboard
  tests.
- `pnpm --filter @casino/client exec tsc --noEmit` clean; `pnpm exec biome check client` clean.
- `git grep -nE "Math\.random|crypto\.getRandomValues" -- client/src/games client/src/components client/src/pixi`
  → nothing.

### S26 — CI: add the `typescript` job + wire `task test`/`contracts`
**Goal:** CI runs the client suite under the existing `ts` paths filter; `task test` includes vitest;
`task contracts` is the real generator (S2).
**Depends on:** S9. (Land after the suite exists; ideally after S25.)

```
Add a `typescript` job to .github/workflows/ci.yml gated on `needs.changes.outputs.ts == 'true'`
(the filter already exists). Steps: checkout; setup pnpm + node; `pnpm install --frozen-lockfile`;
`pnpm exec biome check .`; `pnpm --filter @casino/client exec tsc --noEmit`; the thin-renderer grep gate
(`git grep -nE "Math\\.random|crypto\\.getRandomValues" -- client/src/games client/src/components client/src/pixi`
must be empty — fail the job on any hit); `pnpm --filter @casino/client exec vitest run`;
`pnpm --filter @casino/client build`. Update Taskfile.yml: `test` also runs
`pnpm --filter @casino/client exec vitest run`; confirm `contracts` matches S2. Keep the Python +
rtp-gate jobs unchanged. Update CLAUDE.md "Commands" if needed (same commit). Run Verify, paste output.
```

**Verify:**
- Locally reproduce the job: `pnpm install --frozen-lockfile && pnpm exec biome check . && pnpm --filter @casino/client exec tsc --noEmit && pnpm --filter @casino/client exec vitest run && pnpm --filter @casino/client build`
  → all green.
- The thin-renderer grep gate returns nothing (job would pass).
- `yamllint`/parse of `.github/workflows/ci.yml` valid; Python+rtp-gate jobs untouched (diff shows only
  the added job + Taskfile).

### S27 — Cloudflare Pages config (offline-prepared; not deployed here)
**Goal:** the build is Pages-ready — SPA history fallback, security headers, env wiring, and a verified
`client/dist` — without running any deploy.
**Depends on:** S25, S26.

```
Prepare Cloudflare Pages config WITHOUT deploying (no wrangler login / no `pages deploy`). Read plan
§4.1/§4.5/§9. Add:
- client/public/_redirects: `/*  /index.html  200` (SPA history fallback).
- client/public/_headers: sensible security headers (CSP allowing the API origin + WS, X-Frame-Options,
  etc.) — keep VITE_API_BASE_URL configurable, not baked.
- Document the Pages build settings (build command `pnpm --filter @casino/client build`, output dir
  `client/dist`, the `VITE_API_BASE_URL` env var — unset = demo/mock, set = live API) in a
  client/README section; add client/.env.example with VITE_API_BASE_URL.
- Confirm vite `base: "./"` so assets resolve on Pages.
Use Context7 for current Cloudflare Pages SPA/_headers/_redirects specifics — don't guess. Rules: NO
secrets in source (VITE_API_BASE_URL is public config); do not run a deploy. Run Verify, paste output.
```

**Verify:**
- `pnpm --filter @casino/client build` → `client/dist/` with `index.html`, hashed assets, and the copied
  `_redirects`/`_headers`.
- `_redirects` + `_headers` + `client/.env.example` present; no secret values committed
  (`git grep -niE "secret|token|api[_-]?key" -- client/public client/.env.example` shows only var names).
- No deploy was executed (no wrangler auth/log output).

### S28 — Deploy to Cloudflare Pages (PAID / LIVE — human-authorized, LAST)
**Goal:** the static client is live on Cloudflare Pages; the deployed app loads; a real play-money bet
works against the configured API (or the demo loads with its banner); the verifier link resolves.
**Depends on:** S27 + all game steps.

```
STOP — paid/live operation; the human authorizes/performs the Cloudflare account, billing, and any
secret/CORS handling. This is NOT run in an unattended loop. With the human: create/connect the
Cloudflare Pages project to this repo (or `wrangler pages deploy client/dist`), set the build command
`pnpm --filter @casino/client build`, output `client/dist`, and set VITE_API_BASE_URL to the deployed
API origin for live play (or leave it unset for a demo-mode site). Ensure the backend's CORS allows the
Pages origin (a backend/infra config the human applies — not a client code change). Verify the live URL,
one end-to-end play-money bet (live mode) or the demo banner (demo mode), and that the verifier link
resolves. Record the live URL + go/no-go in 2026-06-28_casino-client_plan.md.
```

**Verify (human-authorized; deferred/gated — not in the offline loop):** the Pages URL loads; in live
mode a play-money bet succeeds end-to-end and `/auth/me` balance updates; in demo mode the banner shows;
the verifier link opens the verifier for a bet; live URL recorded in the plan.

### S29 — Client README, demo GIF, docs index
**Goal:** a reader can run, test, and deploy the client from the docs alone; `CLAUDE.md` + `README.md`
reflect that the client is built.
**Depends on:** S27 (live URL + GIF filled after S28).

```
Write the client docs. Add a client/README.md (run locally: `pnpm install && pnpm --filter
@casino/client dev`; test; build; demo vs live via VITE_API_BASE_URL; deploy to Pages) and a short
"how the thin renderer stays server-authoritative" note linking the FairnessDrawer/verifier. Update root
README.md + CLAUDE.md Map/Status to record the client is built (same commit). Add a 60-second demo GIF
(record after S28 / against the demo build) and link it. Update the docs index to list the four
casino-client docs. Run a link check and paste output.
```

**Verify:** `client/README.md` + updated root `README.md`/`CLAUDE.md` render; the demo GIF is linked; a
Markdown link check across `docs/`, `README.md`, `client/README.md` passes; a fresh reader can run the
client locally from the README alone.

---

## Notes on "standalone & verifiable"

- **Purely additive — nothing is destructive.** There is no decommission step; rollback is "don't merge
  the branch / don't deploy."
- **Every game step is independently verifiable from a server-response FIXTURE** (Vitest + RTL) — no
  step needs a live backend to verify; the mock adapter makes manual play possible. The only
  live/deferred check is **S28** (the paid Pages deploy), gated on a human "go".
- **S10 is a human review gate** (freeze the pattern before scale-out); **S28 is the paid/live gate**.
- If a step's Verify fails, **stop and fix in that step** — never stack the next view on red.
- **CARRY-FORWARD:** S11–S24 each append one lazy-import to `client/src/games/registry.tsx` — serialize
  that one file (rebase the later parallel branch); everything else in those steps is disjoint per-game
  folders and runs in parallel.
