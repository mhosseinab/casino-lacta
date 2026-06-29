// Keno SELECTION math — pure helpers for the player's grid picks and the COSMETIC
// hit highlight. Deliberately contains NO payout math: the multiplier and payout
// come purely off the server `bet.outcome` (rendered verbatim by ResultPanel), and
// the hit DISTRIBUTION is the server's hypergeometric business. Unlike dice/limbo
// there is NO win-chance / multiplier preview here — quoting one would mean
// replicating the server's per-(picks,risk) table, which we never do. `hitsOf` is a
// plain set intersection used ONLY to colour cells; it never decides a payout.

/** Smallest number on the grid. */
export const KENO_GRID_MIN = 1;
/** Largest number on the grid (1..40). */
export const KENO_GRID_MAX = 40;
/** Most numbers a player may select. */
export const KENO_MAX_PICKS = 10;

/** Pure: toggle `n` in `picks`. Removing is always allowed; adding is capped at
 *  `max` (a no-op once full). The result is sorted ascending for stable rendering
 *  and a deterministic request payload. */
export function togglePick(
  picks: number[],
  n: number,
  max = KENO_MAX_PICKS,
): number[] {
  if (picks.includes(n)) {
    return picks.filter((p) => p !== n);
  }
  if (picks.length >= max) {
    return picks;
  }
  return [...picks, n].sort((a, b) => a - b);
}

/** Pure: a selection is bettable when it holds between 1 and 10 picks. The server
 *  stays authoritative on bet limits; this only gates the obviously-empty case. */
export function isValidSelection(picks: number[]): boolean {
  return picks.length >= 1 && picks.length <= KENO_MAX_PICKS;
}

/** Pure: the picks that were drawn (set intersection), sorted ascending. Cosmetic
 *  highlight ONLY — computed over the server-recorded picks/drawn so its size
 *  agrees with the server `hits`; never used to compute a payout. */
export function hitsOf(picks: number[], drawn: number[]): number[] {
  const drawnSet = new Set(drawn);
  return picks.filter((p) => drawnSet.has(p)).sort((a, b) => a - b);
}
