import { lazy } from 'react';
import type { ComponentType, LazyExoticComponent } from 'react';

// =========================================================================== //
// GAME VIEW REGISTRY — the SERIALIZE POINT for the per-game steps (S11..S24).  //
//                                                                             //
// Each game step appends EXACTLY ONE lazy-import line to `gameViews` below,    //
// mapping its `gameId` to a code-split view component. The app shell           //
// (`GameRoute`) looks the id up here; unknown/unregistered ids fall back to    //
// `ComingSoon`. Nothing else in the shell changes when a game lands (OCP).     //
//                                                                             //
// A view is a default-exported React component that depends ONLY on the        //
// `GameClient` transport seam — it renders server-authoritative state and      //
// decides no outcome or balance (CLAUDE.md "thin renderer").                   //
// =========================================================================== //

/** A lazily-loaded game view (default export of a `games/<id>/...` module). */
export type GameView = LazyExoticComponent<ComponentType>;

/**
 * Wraps `React.lazy` so a per-game step appends EXACTLY ONE line to `gameViews`
 * (no separate `import { lazy }` churn per step). The loader code-splits the view
 * into its own chunk.
 */
export function gameView(
  loader: () => Promise<{ default: ComponentType }>,
): GameView {
  return lazy(loader);
}

/**
 * gameId → lazy view. Intentionally EMPTY at S5 — the per-game steps populate it.
 *
 * To register a game, add one line, e.g.:
 *   'originals.dice': gameView(() => import('./dice/DiceView')),
 *
 * >>> APPEND NEW GAME VIEWS BELOW THIS LINE (one per step) <<<
 */
export const gameViews: Record<string, GameView> = {
  'originals.dice': gameView(() => import('./dice/DiceView')),
  'originals.limbo': gameView(() => import('./limbo/LimboView')),
  'originals.pocketdice': gameView(() => import('./pocketdice/PocketDiceView')),
  'originals.keno': gameView(() => import('./keno/KenoView')),
  'originals.roulette': gameView(() => import('./roulette/RouletteView')),
  'originals.mines': gameView(() => import('./mines/MinesView')),
  'originals.hilo': gameView(() => import('./hilo/HiLoView')),
  'originals.plinko': gameView(() => import('./plinko/PlinkoView')),
  'originals.crash': gameView(() => import('./crash/CrashView')),
};

/** True once a game step has registered a view for this id. */
export function hasGameView(gameId: string): boolean {
  return Object.hasOwn(gameViews, gameId);
}
