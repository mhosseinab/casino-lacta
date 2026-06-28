import { type Application, Container, Graphics, Text } from 'pixi.js';
import { useCallback, useEffect, useRef, useState } from 'react';
import { PixiStage } from '../../pixi/PixiStage';
import { useReducedMotion } from '../../pixi/useReducedMotion';

// The dice "roll" visual. PURELY COSMETIC: a marker slides along a 0–100 track and
// settles on the SERVER-decided roll. It never decides the roll — `roll` is the
// server outcome (BetObject.outcome.roll), and the animation only resolves TO it.
// Under prefers-reduced-motion the marker is placed at the roll immediately (no
// animation). The authoritative roll is always shown as text in the DOM, so the
// result is correct regardless of canvas support (jsdom has no WebGL).
//
// REDRAW-ON-NEW-OUTCOME SEAM — the contract S11–S24 copy:
//   PixiStage creates the Pixi Application exactly ONCE and calls `onReady(app)`
//   once (no teardown churn on prop changes). The game stage therefore:
//     1. captures `app` in a ref from onReady, and bumps a `ready` flag, and
//     2. redraws inside a useEffect KEYED ON THE SERVER OUTCOME (`[roll, …]`).
//   Without (2) the marker would draw only for the FIRST bet and go stale on bet
//   #2+ while the DOM shows the new roll. The `ready` bump is load-bearing: the
//   effect runs synchronously on mount BEFORE app.init resolves, so a bare ref
//   would skip the very first draw forever; flipping `ready` in onReady re-runs
//   the effect once the Application exists.
//   Edges (acceptable, deliberate): two IDENTICAL consecutive rolls don't re-key
//   the effect → no re-animation, but the position is unchanged so it still
//   resolves to the server result. drawDiceRoll renders THROUGH the pure
//   `diceMarkerLanding` mapping (unit-tested) — never recompute the roll→x map
//   elsewhere, or the test stops guarding the visual.
const TRACK_MIN = 0;
const TRACK_MAX = 100;
const TRACK_PAD = 16;

/** Resolved marker geometry for a server roll — Pixi-free so it is unit-testable. */
export interface DiceMarkerLanding {
  /** The server roll, clamped into the [0,100] track domain. */
  value: number;
  /** Animation origin: the left end of the track. */
  startX: number;
  /** Final marker x in px — the server-resolved position the visual must reach. */
  endX: number;
  /** True under reduced motion: place at endX immediately, no tween. */
  immediate: boolean;
}

// PURE landing math: maps a server roll (0–100) to the marker's x on a track of
// `width` px. The single owner of the roll→x mapping; drawDiceRoll consumes this,
// and DiceStage.test.ts asserts it directly (jsdom can't run the real renderer).
export function diceMarkerLanding(
  roll: number,
  width: number,
  reducedMotion: boolean,
): DiceMarkerLanding {
  const span = Math.max(1, width - TRACK_PAD * 2);
  const value = Math.min(TRACK_MAX, Math.max(TRACK_MIN, roll));
  const xFor = (v: number): number => TRACK_PAD + (v / TRACK_MAX) * span;
  return {
    value,
    startX: xFor(TRACK_MIN),
    endX: xFor(value),
    immediate: reducedMotion,
  };
}

export function DiceStage(props: { roll: number }) {
  const reducedMotion = useReducedMotion();
  const hasRoll = Number.isFinite(props.roll);
  const rollText = hasRoll ? props.roll.toFixed(2) : '—';

  const appRef = useRef<Application | null>(null);
  const [ready, setReady] = useState(false);

  // onReady fires ONCE (PixiStage owns the Application lifecycle). Capture the app
  // and flip `ready` so the redraw effect runs now that the renderer exists.
  const onReady = useCallback((app: Application) => {
    appRef.current = app;
    setReady(true);
  }, []);

  // Redraw whenever the SERVER outcome changes (or reduced-motion flips). React
  // runs the previous cleanup before the next effect, so this one path handles
  // first draw, redraw-on-new-bet, and unmount. No-op in jsdom (ready stays false).
  useEffect(() => {
    const app = appRef.current;
    if (!ready || !app || !hasRoll) return;
    return drawDiceRoll(app, props.roll, reducedMotion);
  }, [ready, props.roll, hasRoll, reducedMotion]);

  return (
    <div
      data-testid="dice-stage"
      data-static={reducedMotion ? 'true' : 'false'}
      style={{ display: 'grid', gap: 8, maxWidth: 360 }}
    >
      <PixiStage onReady={onReady} className="dice-canvas" />
      <p
        data-testid="dice-roll"
        aria-label="Roll"
        style={{
          margin: 0,
          fontSize: 28,
          fontWeight: 700,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {rollText}
      </p>
    </div>
  );
}

// Cosmetic PixiJS v8 drawing: a track, a settled marker at the roll, and (unless
// reduced-motion) a brief slide-in to that position. Resolves to the server roll
// either way — the marker x comes from `diceMarkerLanding`, never recomputed here.
// Returns a cleanup that removes the ticker BEFORE destroying the layer (so the
// tick never fires on a destroyed Graphics), used for both redraw and unmount.
function drawDiceRoll(
  app: Application,
  roll: number,
  reducedMotion: boolean,
): () => void {
  const width = app.screen.width || 320;
  const trackY = (app.screen.height || 80) / 2;
  const span = Math.max(1, width - TRACK_PAD * 2);
  const { value, startX, endX, immediate } = diceMarkerLanding(
    roll,
    width,
    reducedMotion,
  );

  const layer = new Container();
  app.stage.addChild(layer);

  const track = new Graphics()
    .roundRect(TRACK_PAD, trackY - 3, span, 6, 3)
    .fill({ color: 0x3a3f5a });
  layer.addChild(track);

  const marker = new Graphics().circle(0, 0, 10).fill({ color: 0x4cc38a });
  marker.y = trackY;
  layer.addChild(marker);

  const label = new Text({
    text: value.toFixed(2),
    style: { fill: 0xffffff, fontSize: 16, fontFamily: 'monospace' },
  });
  label.anchor.set(0.5, 1);
  label.y = trackY - 16;
  layer.addChild(label);

  let tick: ((ticker: { deltaMS: number }) => void) | null = null;

  if (immediate) {
    marker.x = endX;
    label.x = endX;
  } else {
    // Cosmetic ease-out slide from the start of the track to the server roll.
    const durationMs = 600;
    let elapsed = 0;
    tick = (ticker: { deltaMS: number }): void => {
      elapsed += ticker.deltaMS;
      const t = Math.min(1, elapsed / durationMs);
      const eased = 1 - (1 - t) * (1 - t);
      marker.x = startX + (endX - startX) * eased;
      label.x = marker.x;
      if (t >= 1 && tick) {
        app.ticker.remove(tick);
        tick = null;
        marker.x = endX; // exact server result, never a timing artefact
        label.x = endX;
      }
    };
    app.ticker.add(tick);
  }

  return () => {
    if (tick) app.ticker.remove(tick);
    layer.destroy({ children: true });
  };
}
