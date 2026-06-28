import { type Application, Container, Graphics, Text } from 'pixi.js';
import { useCallback } from 'react';
import { PixiStage } from '../../pixi/PixiStage';
import { useReducedMotion } from '../../pixi/useReducedMotion';

// The dice "roll" visual. PURELY COSMETIC: a marker slides along a 0–100 track and
// settles on the SERVER-decided roll. It never decides the roll — `roll` is the
// server outcome (BetObject.outcome.roll), and the animation only resolves TO it.
// Under prefers-reduced-motion the marker is placed at the roll immediately (no
// animation). The authoritative roll is always shown as text in the DOM, so the
// result is correct regardless of canvas support (jsdom has no WebGL).
//
// This is the reference pattern S11–S24 copy: read the game-specific outcome key,
// guard it for finiteness (the generic demo transport won't supply it), and animate
// only as decoration over the server truth.
const TRACK_MIN = 0;
const TRACK_MAX = 100;

export function DiceStage(props: { roll: number }) {
  const reducedMotion = useReducedMotion();
  const hasRoll = Number.isFinite(props.roll);
  const rollText = hasRoll ? props.roll.toFixed(2) : '—';

  const onReady = useCallback(
    (app: Application, ctx: { reducedMotion: boolean }) => {
      if (!hasRoll) return;
      const target = Math.min(TRACK_MAX, Math.max(TRACK_MIN, props.roll));
      drawDiceRoll(app, target, ctx.reducedMotion);
    },
    [props.roll, hasRoll],
  );

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
// reduced-motion) a brief slide-in to that position. Resolves to `target` either way.
function drawDiceRoll(
  app: Application,
  target: number,
  reducedMotion: boolean,
): void {
  const width = app.screen.width || 320;
  const trackY = (app.screen.height || 80) / 2;
  const pad = 16;
  const span = Math.max(1, width - pad * 2);
  const xFor = (value: number): number => pad + (value / TRACK_MAX) * span;

  const layer = new Container();
  app.stage.addChild(layer);

  const track = new Graphics()
    .roundRect(pad, trackY - 3, span, 6, 3)
    .fill({ color: 0x3a3f5a });
  layer.addChild(track);

  const marker = new Graphics().circle(0, 0, 10).fill({ color: 0x4cc38a });
  marker.y = trackY;
  layer.addChild(marker);

  const label = new Text({
    text: target.toFixed(2),
    style: { fill: 0xffffff, fontSize: 16, fontFamily: 'monospace' },
  });
  label.anchor.set(0.5, 1);
  label.y = trackY - 16;
  layer.addChild(label);

  const endX = xFor(target);
  if (reducedMotion) {
    marker.x = endX;
    label.x = endX;
    return;
  }

  // Cosmetic ease-out slide from the start of the track to the server roll.
  const startX = xFor(TRACK_MIN);
  const durationMs = 600;
  let elapsed = 0;
  const tick = (ticker: { deltaMS: number }): void => {
    elapsed += ticker.deltaMS;
    const t = Math.min(1, elapsed / durationMs);
    const eased = 1 - (1 - t) * (1 - t);
    marker.x = startX + (endX - startX) * eased;
    label.x = marker.x;
    if (t >= 1) {
      app.ticker.remove(tick);
      marker.x = endX; // exact server result, never a timing artefact
      label.x = endX;
    }
  };
  app.ticker.add(tick);
}
