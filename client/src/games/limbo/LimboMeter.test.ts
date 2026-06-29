import { describe, expect, it } from 'vitest';
import { limboLanding } from './LimboMeter';

describe('limboLanding — pure server-value → meter mapping', () => {
  it('uses the generated multiplier as the end value', () => {
    expect(limboLanding(3.71, false)).toEqual({
      value: 3.71,
      immediate: false,
    });
  });

  it('floors the value at 1.00× (the §2.4 curve floor)', () => {
    expect(limboLanding(0.4, false).value).toBe(1);
    expect(limboLanding(1.0, false).value).toBe(1);
  });

  it('marks immediate under reduced motion (no count-up)', () => {
    expect(limboLanding(5.0, true)).toEqual({ value: 5.0, immediate: true });
  });
});
