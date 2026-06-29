import { describe, expect, it } from 'vitest';
import {
  KENO_GRID_MAX,
  KENO_GRID_MIN,
  KENO_MAX_PICKS,
  hitsOf,
  isValidSelection,
  togglePick,
} from './kenoMath';

// Keno SELECTION math — pure helpers for the player's grid picks and the COSMETIC
// hit highlight. There is NO payout math here: the multiplier/payout come purely
// off the server `bet.outcome` (rendered verbatim). The hit DISTRIBUTION is the
// server's (hypergeometric) business; hitsOf only powers a cosmetic intersection.
describe('kenoMath — pure selection helpers (no payout math)', () => {
  it('the grid is 1..40 and caps at 10 picks', () => {
    expect(KENO_GRID_MIN).toBe(1);
    expect(KENO_GRID_MAX).toBe(40);
    expect(KENO_MAX_PICKS).toBe(10);
  });

  describe('togglePick — add/remove a number, sorted, capped', () => {
    it('adds a number, keeping the selection sorted ascending', () => {
      expect(togglePick([18, 7], 24)).toEqual([7, 18, 24]);
    });

    it('removes a number already selected', () => {
      expect(togglePick([7, 18, 24], 18)).toEqual([7, 24]);
    });

    it('does not exceed the max picks (a no-op once full)', () => {
      const ten = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
      expect(togglePick(ten, 11)).toEqual(ten);
    });

    it('still allows DESELECTING when at the cap', () => {
      const ten = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
      expect(togglePick(ten, 5)).toEqual([1, 2, 3, 4, 6, 7, 8, 9, 10]);
    });

    it('honours a custom max', () => {
      expect(togglePick([1, 2], 3, 2)).toEqual([1, 2]);
    });
  });

  describe('isValidSelection — between 1 and 10 picks', () => {
    it('rejects an empty selection', () => {
      expect(isValidSelection([])).toBe(false);
    });

    it('accepts 1..10 picks', () => {
      expect(isValidSelection([7])).toBe(true);
      expect(isValidSelection([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])).toBe(true);
    });

    it('rejects more than 10 picks', () => {
      expect(isValidSelection([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])).toBe(false);
    });
  });

  describe('hitsOf — pure set intersection (cosmetic highlight only)', () => {
    it('returns the picks that were drawn, sorted ascending', () => {
      expect(
        hitsOf([7, 18, 24, 33, 40], [7, 18, 24, 1, 2, 3, 4, 5, 6, 8]),
      ).toEqual([7, 18, 24]);
    });

    it('is empty when nothing matches', () => {
      expect(hitsOf([7, 18], [1, 2, 3])).toEqual([]);
    });

    it('count matches the server hits for a consistent draw', () => {
      // The cosmetic intersection size must agree with the server `hits` count
      // when computed over the SAME picks/drawn the server recorded.
      const picks = [7, 18, 24, 33, 40];
      const drawn = [7, 18, 24, 1, 2, 3, 4, 5, 6, 8];
      expect(hitsOf(picks, drawn).length).toBe(3);
    });
  });
});
