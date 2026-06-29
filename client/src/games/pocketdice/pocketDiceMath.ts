// Pocket Dice PREVIEW math — the pre-bet quote shown beside the dice (win chance,
// multiplier, potential profit). Presentation-only, mirroring diceMath.ts:
//
//   • Pocket Dice rolls a FAIR 2d6 (triangular pmf, counts out of 36). The win
//     chance is therefore PURELY COMBINATORIAL and edge-INDEPENDENT — unlike Limbo,
//     whose curve bakes the edge into the probability. So winChancePct takes NO
//     edge param (it is a Dice-family game; diceMath.winChancePct is the precedent).
//   • The EDGE enters only the payout multiplier: multiplier = (1 − edge)/p.
//
// The ONE economy input is POCKETDICE_PREVIEW_EDGE — the single named owner of the
// edge in this view, mirroring the server's 1% Originals default. The AUTHORITATIVE
// multiplier, win/loss and payout always come off the server `bet.outcome` (rendered
// verbatim by ResultPanel); this module never decides an outcome. Flagged to review
// as a deliberate preview seam.

export type PocketDiceDirection = 'UNDER' | 'OVER';

/** House edge used for the PREVIEW multiplier quote (mirrors the server 1% default). */
export const POCKETDICE_PREVIEW_EDGE = 0.01;

// 2d6 sum frequencies out of 36 (the triangular pmf). UNDER wins when sum < target,
// OVER wins when sum > target — the target sum itself never wins either way.
const COUNTS: Readonly<Record<number, number>> = {
  2: 1,
  3: 2,
  4: 3,
  5: 4,
  6: 5,
  7: 6,
  8: 5,
  9: 4,
  10: 3,
  11: 2,
  12: 1,
};
const OUTCOMES = 36;

/** Pure: the number of the 36 equally-likely 2d6 outcomes that WIN this bet. Zero for
 *  the forbidden bets (UNDER 2, OVER 12) — they have no winning region. */
function winningOutcomes(
  target: number,
  direction: PocketDiceDirection,
): number {
  let count = 0;
  for (let sum = 2; sum <= 12; sum++) {
    const wins = direction === 'UNDER' ? sum < target : sum > target;
    if (wins) count += COUNTS[sum];
  }
  return count;
}

/** Pure: win probability as a percent (0–100). Combinatorial, edge-free — the player's
 *  true chance of winning a fair 2d6 roll. Mirrors diceMath.winChancePct. */
export function winChancePct(
  target: number,
  direction: PocketDiceDirection,
): number {
  return (winningOutcomes(target, direction) / OUTCOMES) * 100;
}

/** Pure: decimal payout multiplier for the quote, via (1 − edge)/p. Returns 0 for a
 *  forbidden zero-win bet. PREVIEW ONLY — the server stamps the real multiplier. */
export function multiplierFor(
  target: number,
  direction: PocketDiceDirection,
  edge = POCKETDICE_PREVIEW_EDGE,
): number {
  const wins = winningOutcomes(target, direction);
  if (wins <= 0) return 0;
  return (1 - edge) / (wins / OUTCOMES);
}

/** Pure: potential PROFIT in integer minor units = floor(stake × multiplier) − stake.
 *  Floors the payout (money.py rounds DOWN — the truncated fraction is house margin)
 *  and stays integer throughout: no float minor-unit value is ever produced. */
export function profitMinor(
  stakeMinor: number,
  target: number,
  direction: PocketDiceDirection,
  edge = POCKETDICE_PREVIEW_EDGE,
): number {
  const multiplier = multiplierFor(target, direction, edge);
  return Math.floor(stakeMinor * multiplier) - stakeMinor;
}
