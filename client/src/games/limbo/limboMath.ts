// Limbo PREVIEW math — the pre-bet quote shown beside the meter (win chance,
// potential profit). It is presentation-only, exactly like diceMath.ts:
//
//   • winChancePct is a pure function of the chosen target + the house edge.
//   • The PAYOUT multiplier in Limbo simply IS the target (spec §A.3: win pays
//     `stake × target`), so profit is `floor(stake × target) − stake`.
//
// The ONE economy input is LIMBO_PREVIEW_EDGE — the single named owner of the
// edge in this view, mirroring the server's 1% Originals default. The
// AUTHORITATIVE generated multiplier, win/loss, multiplier and payout always come
// off the server `bet.outcome` (rendered verbatim by ResultPanel); this module
// never decides an outcome. Flagged to review as a deliberate preview seam.

/** House edge used for the PREVIEW win-chance quote (mirrors the server 1% default). */
export const LIMBO_PREVIEW_EDGE = 0.01;

/** Smallest valid target — mirrors the engine's `_TARGET_MIN` (limbo.py): the §2.4
 *  curve floors every generated X at 1.00×, so a target ≤ 1.00 is not a gamble. */
export const LIMBO_TARGET_MIN = 1.01;

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

/** Pure: the win probability (percent) for a target multiplier. Spec §A.3:
 *  `P(X ≥ target) = (1 − edge) / target`. Returns 0 for a non-positive target. */
export function winChancePct(
  target: number,
  edge = LIMBO_PREVIEW_EDGE,
): number {
  if (target <= 0) return 0;
  return clamp(((1 - edge) / target) * 100, 0, 100);
}

/** Pure: potential profit (minor units) on a win. The payout multiplier is the
 *  target itself; floor matches the server's round-DOWN money rule. */
export function profitMinor(stakeMinor: number, target: number): number {
  return Math.floor(stakeMinor * target) - stakeMinor;
}
