// Pure dice PREVIEW math — the pre-bet quote shown beside the slider (multiplier,
// win chance, potential profit), exactly as Gamdom/Stake show a live quote as you
// drag the threshold. Spec §A.1: roll ∈ [0,100); UNDER wins if roll < target,
// OVER wins if roll > target; p = winChance/100; multiplier = (1−edge)/p.
//
// THIN-RENDERER BOUNDARY (read before changing):
//   • Win chance is pure BET-PARAMETER math (no economy policy) — free to compute.
//   • multiplier / potential profit require the house EDGE. The AUTHORITATIVE
//     multiplier & payout are ALWAYS server-stamped on `bet.outcome` (the view
//     renders those verbatim). `DICE_PREVIEW_EDGE` below drives the pre-bet QUOTE
//     ONLY. It is the single named owner of the edge in the client; if the API
//     later exposes GameConfig.edge, source it here instead of the constant.
//   This is a deliberate preview seam, not embedded payout logic — flagged to review.

export type DiceDirection = 'OVER' | 'UNDER';

/**
 * Preview-only house edge. Mirrors the server's Originals default (spec §0.4 / §A.1
 * = 1%). NOT authoritative: the server stamps the real multiplier on the result.
 */
export const DICE_PREVIEW_EDGE = 0.01;

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

/**
 * Win probability as a percent (0–100). Pure bet-parameter math, no economy policy.
 * OVER wins above the target → p = 100 − target; UNDER wins below → p = target.
 */
export function winChancePct(target: number, direction: DiceDirection): number {
  const t = clamp(target, 0, 100);
  return direction === 'OVER' ? 100 - t : t;
}

/**
 * Decimal payout multiplier for a win chance (percent), via (1−edge)/p. Returns 0
 * for an impossible bet (winPct ≤ 0). PREVIEW ONLY — see the file header.
 */
export function multiplierFor(
  winPct: number,
  edge = DICE_PREVIEW_EDGE,
): number {
  if (winPct <= 0) return 0;
  return (1 - edge) / (winPct / 100);
}

/**
 * Potential PROFIT in integer minor units = floor(stake × multiplier) − stake.
 * Floors the payout (money.py rounds DOWN — the truncated fraction is house margin)
 * and stays integer throughout: no float minor-unit value is ever produced.
 */
export function profitMinor(stakeMinor: number, multiplier: number): number {
  return Math.floor(stakeMinor * multiplier) - stakeMinor;
}
