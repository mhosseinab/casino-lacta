---
name: add-game-ui
description: >-
  Recipe for adding a thin-client (React/Vite) renderer view for a game — render server-authoritative
  state, format minor units at display, keep animations cosmetic, surface provably-fair + the
  play-money/RG badge, and cover it with Vitest + React Testing Library component tests (NO
  Playwright/E2E at this stage). Use when building any client/ game view. Companion to add-game
  (the backend recipe).
---

# Adding a game's client view

The `client/` (Vite + React + TypeScript, canvas/PixiJS visuals) is **presentation only** — it
renders what the server decides and never computes an outcome or a balance (build-plan §2). A game
view is done when it displays the server's result faithfully, surfaces fairness + the play-money
badge, and is covered by component tests. Read `CLAUDE.md` and the game's spec § first. For React /
Vite / PixiJS / Vitest / React Testing Library API specifics, consult **Context7**
(`resolve-library-id` → `query-docs`) — don't guess framework syntax.

## TDD first (non-negotiable)

**RED → GREEN → REFACTOR**, here too. Write the Vitest + React Testing Library component test from a
server-response FIXTURE BEFORE building the view: assert it renders the fixture's amounts (formatted
from minor units) and the server outcome verbatim. Run it; confirm it FAILS (component missing).
Then build the simplest view that passes, then refactor green. No outcome logic ever enters the
component to make a test pass. (No Playwright/E2E at this stage.)

## Recipe

1. **Drive from server state, never decide.**
   - Place a bet → `POST /games/{id}/bet`; render the returned bet object (outcome, multiplier,
     payout) exactly as received. For stateful games use `/games/{id}/action` and `GET
     /games/{id}/state` (resume). For Crash/poker, subscribe to the WS and render `round`/`tick`/
     `crash` (or poker table) events.
   - The client holds NO RNG, payout math, RTP, or balance accumulation. The displayed balance is
     a server field. (If a value isn't in a server response, the client must not invent it.)

2. **Money: minor units in, format at display.** Server amounts are integer minor units; render via
   a shared `formatMinor(amountMinor, currency)` util — never float math on money. Stake inputs are
   validated to integer minor units and sent as integers; reject float entry at the form boundary.

3. **Animation is cosmetic and resolves to the server outcome.** The Crash curve, Plinko ball, and
   reels are visuals only; the result is fixed server-side. Animate TOWARD the server result (ball
   ends in the server bin; curve busts at the server `C`) — never read the result off the animation
   or let timing change it.

4. **Surface provably-fair.** Show the `serverSeedHash` commit before the bet, the revealed seed
   after rotation, and a link to `/fairness/{betId}` (the open verifier). This is the differentiated
   feature — make it visible, not buried.

5. **Responsible-gaming + identity.** Render the persistent "play money — not real / no prizes"
   badge on every screen. Surface typed RG block reasons (limit / cool-off / self-exclusion) and
   reality-check prompts; never swallow a blocked-bet error into a generic message.

6. **Realtime hygiene.** Handle WS reconnect/resync (re-pull `state` on resume). The poker view
   renders only the cards the server sent for this seat — never reconstruct or assume hidden cards.

7. **Tests — Vitest + React Testing Library, component-level.** Render the view from a server-response
   FIXTURE and assert: amounts are formatted from minor units, the server outcome is shown verbatim,
   error/RG-block states render, and NO outcome is computed in the component. **Do not add
   Playwright / browser E2E at this stage** (too heavy) — fixtures + RTL cover the renderer.

8. **Per commit:** lint/format (project's eslint/prettier) clean; types mirror the server response
   shapes (no `any` on money/state); no secrets in client source; Conventional Commits
   (`feat(client): dice view`).

## Done when
The view renders a real play-money bet end-to-end from server responses, formats minor units only
at display, keeps animation cosmetic, surfaces the verifier link + play-money badge, and passes its
Vitest/RTL component tests. It contains zero outcome/balance logic — that lives only on the server.
