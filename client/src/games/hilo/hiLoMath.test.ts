import { describe, expect, it } from 'vitest';
import { canCashout, pHigher, pLower, rankLabel } from './hiLoMath';

// Pure HiLo PREVIEW helpers (spec §A.7). These are presentation-only: the
// AUTHORITATIVE win/loss + multipliers always come off the server projection.
// A tie (revealed === shown) wins for EITHER chosen side, which is why the two
// side probabilities sum to > 1 (the shown rank is counted for both sides).

describe('rankLabel', () => {
  it('labels the face/ace ranks', () => {
    expect(rankLabel(1)).toBe('A');
    expect(rankLabel(11)).toBe('J');
    expect(rankLabel(12)).toBe('Q');
    expect(rankLabel(13)).toBe('K');
  });

  it('labels the pip ranks 2..10 as their number', () => {
    expect(rankLabel(2)).toBe('2');
    expect(rankLabel(9)).toBe('9');
    expect(rankLabel(10)).toBe('10');
  });
});

describe('pHigher / pLower — tie counts for BOTH sides', () => {
  it('uses (14-r)/13 for higher-or-same and r/13 for lower-or-same', () => {
    expect(pHigher(1)).toBeCloseTo(13 / 13);
    expect(pLower(1)).toBeCloseTo(1 / 13);
    expect(pHigher(13)).toBeCloseTo(1 / 13);
    expect(pLower(13)).toBeCloseTo(13 / 13);
  });

  it('a middle rank — the two sides sum to > 1 (the tie is double-counted)', () => {
    expect(pHigher(7)).toBeCloseTo(7 / 13);
    expect(pLower(7)).toBeCloseTo(7 / 13);
    expect(pHigher(7) + pLower(7)).toBeGreaterThan(1);
  });
});

describe('canCashout — requires at least one successful guess', () => {
  it('is false at zero steps and true once a guess has landed', () => {
    expect(canCashout(0)).toBe(false);
    expect(canCashout(1)).toBe(true);
    expect(canCashout(5)).toBe(true);
  });
});
