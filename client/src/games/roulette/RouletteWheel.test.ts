import { describe, expect, it } from 'vitest';
import { rouletteLanding } from './RouletteWheel';

// Pure landing math for the cosmetic spin. jsdom has no real renderer, so "the
// cosmetic display resolves to the SERVER result/colour" is proven through the pure
// function the render consumes (same discipline as S9 DiceSlider / S11 LimboMeter).
describe('rouletteLanding — server result/colour → spin landing (pure)', () => {
  it('passes the server result + colour through verbatim', () => {
    expect(rouletteLanding(73, 'BLACK', false)).toEqual({
      result: 73,
      colour: 'BLACK',
      immediate: false,
    });
  });

  it('clamps the result into the 0–99 pocket domain', () => {
    expect(rouletteLanding(150, 'RED', false).result).toBe(99);
    expect(rouletteLanding(-5, 'GREEN', false).result).toBe(0);
  });

  it('reduced motion resolves immediately to the same result', () => {
    const animated = rouletteLanding(42, 'RED', false);
    const reduced = rouletteLanding(42, 'RED', true);
    expect(reduced.result).toBe(animated.result);
    expect(reduced.immediate).toBe(true);
    expect(animated.immediate).toBe(false);
  });
});
