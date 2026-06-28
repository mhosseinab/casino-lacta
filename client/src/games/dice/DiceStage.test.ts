import { describe, expect, it } from 'vitest';
import { diceMarkerLanding } from './DiceStage';

// Pure landing-math unit tests. jsdom has NO WebGL, so the Pixi draw path
// (drawDiceRoll/onReady) never runs under Vitest — the ONLY way to prove "the
// cosmetic marker resolves to the SERVER roll" is to test the pure function that
// maps a server roll (0–100 domain) to the marker's end x. drawDiceRoll renders
// THROUGH this function, so guarding it guards the visual. The 15 later games
// inherit this: a tiny pure mapping + this test shape.
describe('diceMarkerLanding — server roll → marker x (pure, Pixi-free)', () => {
  const WIDTH = 320; // pad=16 each side → span=288
  const PAD = 16;
  const SPAN = WIDTH - PAD * 2;

  it('maps the domain endpoints to the track ends', () => {
    expect(diceMarkerLanding(0, WIDTH, false).endX).toBe(PAD); // 16
    expect(diceMarkerLanding(100, WIDTH, false).endX).toBe(PAD + SPAN); // 304
  });

  it('maps a representative interior roll linearly', () => {
    // 50/100 of the span past the left pad.
    expect(diceMarkerLanding(50, WIDTH, false).endX).toBe(PAD + SPAN * 0.5); // 160
    expect(diceMarkerLanding(25, WIDTH, false).endX).toBe(PAD + SPAN * 0.25); // 88
  });

  it('clamps rolls outside the 0–100 domain to the track ends', () => {
    expect(diceMarkerLanding(150, WIDTH, false).value).toBe(100);
    expect(diceMarkerLanding(150, WIDTH, false).endX).toBe(PAD + SPAN);
    expect(diceMarkerLanding(-5, WIDTH, false).value).toBe(0);
    expect(diceMarkerLanding(-5, WIDTH, false).endX).toBe(PAD);
  });

  it('animation starts at the track origin (left pad), not the roll', () => {
    expect(diceMarkerLanding(80, WIDTH, false).startX).toBe(PAD);
  });

  it('reduced motion resolves to the FINAL position immediately (no tween)', () => {
    const animated = diceMarkerLanding(73.5, WIDTH, false);
    const reduced = diceMarkerLanding(73.5, WIDTH, true);
    // Same server roll → identical end position whether animated or not: the
    // visual always resolves to the server result.
    expect(reduced.endX).toBe(animated.endX);
    expect(reduced.immediate).toBe(true);
    expect(animated.immediate).toBe(false);
  });
});
