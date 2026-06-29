import { describe, expect, it } from 'vitest';
import {
  POCKETS,
  ROULETTE_PREVIEW_EDGE,
  profitMinor,
  winChancePct,
} from './rouletteMath';

// Roulette 0–99 PREVIEW math (spec §A.9). Presentation-only quote, exactly like
// limboMath.ts: win chance is the pure pocket probability; the win multiplier is
// (1 − edge) / p with p = pockets/100. The AUTHORITATIVE multiplier/payout always
// come off the server `bet.outcome` (rendered verbatim); this module decides nothing.
describe('rouletteMath — pure preview quote (spec §A.9)', () => {
  it('uses a 1% preview edge, mirroring the server default', () => {
    expect(ROULETTE_PREVIEW_EDGE).toBe(0.01);
  });

  it('exposes the seeded pocket counts (1 green / 49 red / 50 black)', () => {
    expect(POCKETS).toEqual({ GREEN: 1, RED: 49, BLACK: 50 });
  });

  describe('winChancePct — pocket probability (edge-independent)', () => {
    it.each([
      ['GREEN', 1.0],
      ['RED', 49.0],
      ['BLACK', 50.0],
    ] as const)('%s → %f%%', (colour, pct) => {
      expect(winChancePct(colour)).toBeCloseTo(pct, 2);
    });

    it('is independent of the edge (probability, not payout)', () => {
      expect(winChancePct('RED', 0.05)).toBeCloseTo(49.0, 2);
    });
  });

  describe('profitMinor — win pays stake × (1 − edge)/p, floored', () => {
    it('GREEN (~99×): 100 → 9800 profit', () => {
      // floor(100 × 0.99/0.01) − 100 = 9900 − 100
      expect(profitMinor(100, 'GREEN')).toBe(9800);
    });

    it('RED (~2.0204×): 100 → 102 profit (round DOWN)', () => {
      // floor(100 × 0.99/0.49) = floor(202.04…) = 202 → 102
      expect(profitMinor(100, 'RED')).toBe(102);
    });

    it('BLACK (1.98× exactly): 100 → 98 profit', () => {
      // floor(100 × 0.99/0.50) = 198 → 98
      expect(profitMinor(100, 'BLACK')).toBe(98);
    });

    it('honours a custom edge', () => {
      // floor(100 × 0.98/0.50) − 100 = 196 − 100
      expect(profitMinor(100, 'BLACK', 0.02)).toBe(96);
    });
  });
});
