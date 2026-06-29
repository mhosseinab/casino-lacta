import { describe, expect, it } from 'vitest';
import {
  POCKETDICE_PREVIEW_EDGE,
  type PocketDiceDirection,
  multiplierFor,
  profitMinor,
  winChancePct,
} from './pocketDiceMath';

// Pocket Dice is a FAIR 2d6 game (triangular pmf, counts/36) — unlike Limbo, the
// win chance is purely combinatorial and edge-INDEPENDENT; the edge applies only to
// the payout multiplier. So winChancePct mirrors diceMath (no edge param); the named
// edge owner POCKETDICE_PREVIEW_EDGE drives only the multiplier/profit quote.
describe('pocketDiceMath — pure preview quote (spec §A, 2d6 triangular pmf)', () => {
  it('uses a 1% preview edge, mirroring the server default', () => {
    expect(POCKETDICE_PREVIEW_EDGE).toBe(0.01);
  });

  describe('winChancePct — p = (count in winning region)/36 × 100, edge-free', () => {
    // counts: {2:1,3:2,4:3,5:4,6:5,7:6,8:5,9:4,10:3,11:2,12:1} (sum 36).
    it.each<[number, PocketDiceDirection, number]>([
      [7, 'UNDER', (15 / 36) * 100], // sums 2..6 = 1+2+3+4+5 = 15
      [7, 'OVER', (15 / 36) * 100], // sums 8..12 = 5+4+3+2+1 = 15 (7 itself loses both)
      [4, 'UNDER', (3 / 36) * 100], // sums 2,3 = 1+2 = 3
      [10, 'OVER', (3 / 36) * 100], // sums 11,12 = 2+1 = 3
      [8, 'UNDER', (21 / 36) * 100], // sums 2..7 = 21
      [6, 'OVER', (21 / 36) * 100], // sums 7..12 = 21
      [12, 'UNDER', (35 / 36) * 100], // sums 2..11 = 35
      [2, 'OVER', (35 / 36) * 100], // sums 3..12 = 35
      [3, 'UNDER', (1 / 36) * 100], // sum 2 = 1
      [11, 'OVER', (1 / 36) * 100], // sum 12 = 1
    ])('%i %s → %f%%', (target, direction, pct) => {
      expect(winChancePct(target, direction)).toBeCloseTo(pct, 6);
    });

    it('returns 0 for the forbidden zero-win-region bets (UNDER 2, OVER 12)', () => {
      expect(winChancePct(2, 'UNDER')).toBe(0);
      expect(winChancePct(12, 'OVER')).toBe(0);
    });
  });

  describe('multiplierFor — (1 − edge)/p, the preview seam', () => {
    it('applies the default 1% edge to the fair win probability', () => {
      expect(multiplierFor(7, 'UNDER')).toBeCloseTo(0.99 / (15 / 36), 6); // 2.376
      expect(multiplierFor(4, 'UNDER')).toBeCloseTo(0.99 / (3 / 36), 6); // 11.88
    });

    it('honours a custom edge (e.g. 0 → fair 1/p)', () => {
      expect(multiplierFor(7, 'UNDER', 0)).toBeCloseTo(36 / 15, 6); // 2.4
    });

    it('returns 0 for a forbidden zero-win bet', () => {
      expect(multiplierFor(2, 'UNDER')).toBe(0);
      expect(multiplierFor(12, 'OVER')).toBe(0);
    });
  });

  describe('profitMinor — floor(stake × multiplier) − stake (round DOWN)', () => {
    it('floors the payout then subtracts the stake', () => {
      // 100 × 2.376 = 237.6 → floor 237 → profit 137
      expect(profitMinor(100, 7, 'UNDER')).toBe(137);
      // 1000 × 2.376 = 2376 → profit 1376
      expect(profitMinor(1000, 7, 'UNDER')).toBe(1376);
      // 100 × 11.88 = 1188 → profit 1088
      expect(profitMinor(100, 4, 'UNDER')).toBe(1088);
    });

    it('truncates a fractional minor-unit payout', () => {
      // mult(8,UNDER) = 0.99/(21/36) = 1.697142… → 100× = 169.71 → floor 169 → 69
      expect(profitMinor(100, 8, 'UNDER')).toBe(69);
    });
  });
});
