// HiLo PREVIEW math — presentation-only helpers for the thin renderer (spec §A.7).
//
// These compute NO settlement: the authoritative win/loss, stepMultiplier and
// currentMultiplier always arrive on the server's /action projection and are
// rendered verbatim. This module only labels a card and quotes each side's win
// probability as a pure hint:
//
//   p(Higher-or-same) = (14 - r) / 13     # for shown rank r ∈ 1..13
//   p(Lower-or-same)  = r / 13
//
// A tie (revealed === shown) wins for EITHER chosen side, so the two side
// probabilities sum to > 1 — the shown rank is counted for both sides. We never
// derive the multiplier from these (that is the server's job).

const _RANKS = 13;

/** Rank 1..13 → display label: A, 2..10, J, Q, K. */
export function rankLabel(rank: number): string {
  switch (rank) {
    case 1:
      return 'A';
    case 11:
      return 'J';
    case 12:
      return 'Q';
    case 13:
      return 'K';
    default:
      return String(rank);
  }
}

/** Pure preview: p(Higher-or-same) = (14 - r) / 13 for shown rank r. */
export function pHigher(rank: number): number {
  return (14 - rank) / _RANKS;
}

/** Pure preview: p(Lower-or-same) = r / 13 for shown rank r. */
export function pLower(rank: number): number {
  return rank / _RANKS;
}

/** Cash-out requires at least one successful guess (mirrors the engine fence). */
export function canCashout(steps: number): boolean {
  return steps >= 1;
}
