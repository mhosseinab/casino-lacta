import { describe, expect, it } from 'vitest';
import {
  DICE_PREVIEW_EDGE,
  multiplierFor,
  profitMinor,
  winChancePct,
} from './diceMath';

// The dice PREVIEW quote (multiplier / win chance / potential profit). These pin the
// spec §A.1 math AND the thin-renderer boundary: win chance is pure bet-parameter
// math; the multiplier uses the preview edge that mirrors the server default. The
// authoritative multiplier still comes off the server result — these only quote it.
describe('winChancePct — pure bet-parameter probability', () => {
  it('OVER wins above the target: p = 100 − target', () => {
    expect(winChancePct(50, 'OVER')).toBe(50);
    expect(winChancePct(98, 'OVER')).toBe(2);
    expect(winChancePct(50.5, 'OVER')).toBeCloseTo(49.5, 10);
  });

  it('UNDER wins below the target: p = target', () => {
    expect(winChancePct(25, 'UNDER')).toBe(25);
    expect(winChancePct(2, 'UNDER')).toBe(2);
  });

  it('clamps the target into the 0–100 domain', () => {
    expect(winChancePct(150, 'OVER')).toBe(0);
    expect(winChancePct(-5, 'UNDER')).toBe(0);
  });
});

describe('multiplierFor — (1−edge)/p, preview edge mirrors server 1%', () => {
  it('reproduces the spec §A.1 verified rows (edge 1%)', () => {
    expect(multiplierFor(50)).toBeCloseTo(1.98, 10); // UNDER 50
    expect(multiplierFor(25)).toBeCloseTo(3.96, 10); // UNDER 25
    expect(multiplierFor(2)).toBeCloseTo(49.5, 10); // OVER 98
  });

  it('reproduces the Gamdom reference: OVER 50.50 → 49.50% → 2.00×', () => {
    const p = winChancePct(50.5, 'OVER');
    expect(multiplierFor(p)).toBeCloseTo(2.0, 10);
  });

  it('uses the named preview edge constant (= 1%)', () => {
    expect(DICE_PREVIEW_EDGE).toBe(0.01);
  });

  it('returns 0 for an impossible bet (no win chance)', () => {
    expect(multiplierFor(0)).toBe(0);
    expect(winChancePct(100, 'OVER')).toBe(0);
  });
});

describe('profitMinor — integer minor units, floored payout', () => {
  it('profit = floor(stake × multiplier) − stake', () => {
    expect(profitMinor(100, 2)).toBe(100); // 200 payout − 100 stake
    expect(profitMinor(100, 1.98)).toBe(98); // floor(198) − 100
  });

  it('floors the fractional minor unit (never a float minor value)', () => {
    // 333 × 1.98 = 659.34 → floor 659 → profit 326. Integer throughout.
    expect(profitMinor(333, 1.98)).toBe(326);
    expect(Number.isInteger(profitMinor(333, 1.98))).toBe(true);
  });
});
