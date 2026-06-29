import { describe, expect, it } from 'vitest';
import {
  LIMBO_PREVIEW_EDGE,
  LIMBO_TARGET_MIN,
  profitMinor,
  winChancePct,
} from './limboMath';

describe('limboMath — pure preview quote (spec §A.3)', () => {
  it('uses a 1% preview edge, mirroring the server default', () => {
    expect(LIMBO_PREVIEW_EDGE).toBe(0.01);
  });

  it('floors the smallest target at 1.01 (engine _TARGET_MIN)', () => {
    expect(LIMBO_TARGET_MIN).toBe(1.01);
  });

  describe('winChancePct — P(X ≥ target) = (1 − edge)/target', () => {
    // Spec §A.3 payout examples (edge 1%).
    it.each([
      [1.5, 66.0],
      [2.0, 49.5],
      [10.0, 9.9],
      [100.0, 0.99],
    ])('target %f → ~%f%%', (target, pct) => {
      expect(winChancePct(target)).toBeCloseTo(pct, 2);
    });

    it('honours a custom edge (e.g. Gamdom 2% → 49.00% for 2.00×)', () => {
      expect(winChancePct(2.0, 0.02)).toBeCloseTo(49.0, 2);
    });

    it('clamps to [0,100] and returns 0 for a non-positive target', () => {
      expect(winChancePct(0)).toBe(0);
      expect(winChancePct(-5)).toBe(0);
      expect(winChancePct(1.0)).toBe(99); // (0.99/1)*100, still ≤ 100
    });
  });

  describe('profitMinor — payout multiplier IS the target, floored', () => {
    it('floors stake × target then subtracts the stake', () => {
      expect(profitMinor(100, 2.0)).toBe(100); // 200 − 100
      expect(profitMinor(100, 1.5)).toBe(50); // 150 − 100
    });

    it('truncates a fractional minor-unit payout (round DOWN)', () => {
      // 100 × 2.005 = 200.5 → floor 200 → profit 100
      expect(profitMinor(100, 2.005)).toBe(100);
    });
  });
});
