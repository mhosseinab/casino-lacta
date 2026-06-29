// Roulette 0–99 PREVIEW math — the pre-bet quote shown beside the colour picker
// (win chance, potential profit). Presentation-only, exactly like limboMath.ts:
//
//   • winChancePct is the pure pocket probability for a colour (p = pockets/100),
//     independent of the edge — the edge moves the payout, not the odds.
//   • The win multiplier is `(1 − edge) / p`, so profit on a win is
//     `floor(stake × (1 − edge)/p) − stake` (floor = the server's round-DOWN rule).
//
// The ONE economy input is ROULETTE_PREVIEW_EDGE — the single named owner of the
// edge in this view, mirroring the server's 1% Originals default. The AUTHORITATIVE
// result, colour, multiplier and payout always come off the server `bet.outcome`
// (rendered verbatim by ResultPanel + the spin display); this module never decides
// an outcome. Flagged to review as a deliberate preview seam.

/** House edge used for the PREVIEW profit quote (mirrors the server 1% default). */
export const ROULETTE_PREVIEW_EDGE = 0.01;

/** The three colour picks. UPPERCASE keys match the engine's pocket map (§A.9). */
export type RouletteColour = 'GREEN' | 'RED' | 'BLACK';

/** Pocket counts from the seeded GameConfig: 1 green / 49 red / 50 black (partition
 *  of {0,…,99}). Authoritative copy is server config; this is the display mirror. */
export const POCKETS: Record<RouletteColour, number> = {
  GREEN: 1,
  RED: 49,
  BLACK: 50,
};

/** Pure: the win probability (percent) for a colour — `pockets/100 × 100`. The edge
 *  param is accepted for signature parity with the sibling games but is unused: the
 *  odds are the pocket fraction, the edge only scales the payout. */
export function winChancePct(
  colour: RouletteColour,
  _edge = ROULETTE_PREVIEW_EDGE,
): number {
  return (POCKETS[colour] / 100) * 100;
}

/** Pure: potential profit (minor units) on a win. Win pays `stake × (1 − edge)/p`
 *  with `p = pockets/100`; floor matches the server's round-DOWN money rule. */
export function profitMinor(
  stakeMinor: number,
  colour: RouletteColour,
  edge = ROULETTE_PREVIEW_EDGE,
): number {
  const p = POCKETS[colour] / 100;
  return Math.floor((stakeMinor * (1 - edge)) / p) - stakeMinor;
}
