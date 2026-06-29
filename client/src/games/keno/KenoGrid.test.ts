import { describe, expect, it } from 'vitest';
import { cellState, kenoReveal } from './KenoGrid';

// Pure cosmetic mapping for the grid. jsdom can't run a real reveal animation, so
// the load-bearing seams — "server drawn set → reveal" and "cell appearance for a
// number" — are proven through the pure functions the render consumes (S9 discipline).
describe('kenoReveal — server drawn set → cosmetic reveal (pure)', () => {
  it('preserves the server drawn set verbatim', () => {
    const drawn = [7, 18, 24, 1, 2, 3, 4, 5, 6, 8];
    expect(kenoReveal(drawn, false).drawn).toEqual(drawn);
  });

  it('reduced motion resolves immediately; animated does not', () => {
    expect(kenoReveal([7, 18], true).immediate).toBe(true);
    expect(kenoReveal([7, 18], false).immediate).toBe(false);
  });
});

describe('cellState — appearance for a single grid number (pure)', () => {
  const picks = [7, 18, 24];

  it('is "idle" before a draw for an unpicked number', () => {
    expect(cellState(40, picks, null)).toBe('idle');
  });

  it('is "pick" before a draw for a picked number', () => {
    expect(cellState(7, picks, null)).toBe('pick');
  });

  it('is "hit" for a picked number that was drawn', () => {
    expect(cellState(7, picks, [7, 1, 2])).toBe('hit');
  });

  it('is "pick" (a missed pick) for a picked number not drawn', () => {
    expect(cellState(18, picks, [7, 1, 2])).toBe('pick');
  });

  it('is "drawn" for an unpicked number that was drawn', () => {
    expect(cellState(1, picks, [7, 1, 2])).toBe('drawn');
  });
});
