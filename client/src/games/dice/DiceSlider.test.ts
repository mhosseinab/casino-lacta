import { describe, expect, it } from 'vitest';
import { diceResultLanding, winRegion } from './DiceSlider';

// Pure geometry for the slider. jsdom has no real layout, so "the cosmetic marker
// resolves to the SERVER roll" and "the win band matches the target/direction" are
// proven through the pure functions the render consumes (same discipline as S9).
describe('winRegion — green (win) band for target + direction', () => {
  it('OVER wins to the RIGHT of the target', () => {
    expect(winRegion(70, 'OVER')).toEqual({ fromPct: 70, toPct: 100 });
  });

  it('UNDER wins to the LEFT of the target', () => {
    expect(winRegion(30, 'UNDER')).toEqual({ fromPct: 0, toPct: 30 });
  });

  it('clamps the target into the 0–100 track domain', () => {
    expect(winRegion(150, 'OVER')).toEqual({ fromPct: 100, toPct: 100 });
    expect(winRegion(-5, 'UNDER')).toEqual({ fromPct: 0, toPct: 0 });
  });
});

describe('diceResultLanding — server roll → marker position (pure)', () => {
  it('maps the roll directly to a track percent', () => {
    expect(diceResultLanding(42.13, false).leftPct).toBeCloseTo(42.13, 10);
    expect(diceResultLanding(0, false).leftPct).toBe(0);
    expect(diceResultLanding(100, false).leftPct).toBe(100);
  });

  it('clamps rolls outside the 0–100 domain', () => {
    expect(diceResultLanding(150, false).value).toBe(100);
    expect(diceResultLanding(-5, false).value).toBe(0);
  });

  it('reduced motion resolves to the final position immediately', () => {
    const animated = diceResultLanding(73.5, false);
    const reduced = diceResultLanding(73.5, true);
    expect(reduced.leftPct).toBe(animated.leftPct);
    expect(reduced.immediate).toBe(true);
    expect(animated.immediate).toBe(false);
  });
});
