import { useEffect, useState } from 'react';
import { useReducedMotion } from '../../pixi/useReducedMotion';
import type { DiceDirection } from './diceMath';

// The dice threshold slider — the Gamdom-style red/green bar. It serves DOUBLE duty,
// and the two roles are kept strictly separate:
//   • THUMB  = the player's chosen target (INPUT). Decides nothing; a draggable
//     range control that emits onTargetChange. The win/lose split colours the track.
//   • MARKER = the SERVER roll (OUTPUT). A purely cosmetic pip that lands on
//     `roll` (BetObject.outcome.roll) and only resolves TO it — it never decides it.
//
// Carried over from the S9 reference pattern (DOM here instead of Pixi, but the same
// load-bearing ideas the 15 later games inherit):
//   1. PURE, unit-tested geometry (winRegion / diceResultLanding) — jsdom can't run
//      a real renderer, so the mapping "server roll → marker position" is proven by
//      testing the pure function the render consumes.
//   2. Reduced motion → place the marker at the result IMMEDIATELY (no tween).
//   3. Re-animation is keyed on BET IDENTITY (betId), not the roll value, so two
//      identical consecutive rolls still replay from the origin.

export interface WinRegion {
  /** Left edge of the WIN band, as a track percent (0–100). */
  fromPct: number;
  /** Right edge of the WIN band, as a track percent (0–100). */
  toPct: number;
}

/** Pure: the green (win) band for a target+direction. OVER wins to the right of the
 *  target, UNDER to the left. The complement is the red (lose) band. */
export function winRegion(target: number, direction: DiceDirection): WinRegion {
  const t = Math.min(100, Math.max(0, target));
  return direction === 'OVER'
    ? { fromPct: t, toPct: 100 }
    : { fromPct: 0, toPct: t };
}

export interface DiceResultLanding {
  /** The server roll, clamped into the [0,100] track domain. */
  value: number;
  /** Marker position along the track, as a percent (0–100). */
  leftPct: number;
  /** True under reduced motion: place at leftPct immediately, no tween. */
  immediate: boolean;
}

/** Pure: maps a server roll (0–100 domain) to the marker's track percent. The single
 *  owner of the roll→position mapping; DiceSlider renders THROUGH it. */
export function diceResultLanding(
  roll: number,
  reducedMotion: boolean,
): DiceResultLanding {
  const value = Math.min(100, Math.max(0, roll));
  return { value, leftPct: value, immediate: reducedMotion };
}

const RED = '#e0364f';
const GREEN = '#1bd96a';
const TICKS = [0, 25, 50, 75, 100];

export function DiceSlider(props: {
  target: number;
  direction: DiceDirection;
  onTargetChange: (target: number) => void;
  roll?: number | null;
  betId?: string | null;
  won?: boolean;
}) {
  const reducedMotion = useReducedMotion();
  const { target, direction, onTargetChange } = props;
  const hasRoll = props.roll != null && Number.isFinite(props.roll);

  // Track gradient: hard stop at the target (the win/lose split). OVER → red then
  // green; UNDER → green then red. The win band itself is winRegion() (exported +
  // unit-tested); the gradient is the same boundary rendered.
  const track =
    direction === 'OVER'
      ? `linear-gradient(to right, ${RED} 0 ${target}%, ${GREEN} ${target}% 100%)`
      : `linear-gradient(to right, ${GREEN} 0 ${target}%, ${RED} ${target}% 100%)`;

  return (
    <div
      data-testid="dice-stage"
      data-static={reducedMotion ? 'true' : 'false'}
      style={{ display: 'grid', gap: 18, padding: '36px 8px 8px' }}
    >
      <div style={{ position: 'relative', height: 28 }}>
        {/* The coloured win/lose track. */}
        <div
          aria-hidden
          style={{
            position: 'absolute',
            top: 9,
            left: 0,
            right: 0,
            height: 10,
            borderRadius: 999,
            background: track,
          }}
        />

        {/* The SERVER-roll marker (cosmetic). Keyed by betId → replays each bet. */}
        {hasRoll && (
          <ResultMarker
            key={props.betId ?? String(props.roll)}
            landing={diceResultLanding(props.roll as number, reducedMotion)}
            won={props.won}
          />
        )}

        {/* The visible thumb, positioned at the target (INPUT). */}
        <div
          aria-hidden
          style={{
            position: 'absolute',
            top: 0,
            left: `${target}%`,
            transform: 'translateX(-50%)',
            width: 30,
            height: 28,
            borderRadius: 8,
            background: '#f4f6fb',
            boxShadow: '0 2px 6px rgba(0,0,0,0.45)',
            display: 'grid',
            placeItems: 'center',
            color: '#5b6172',
            fontSize: 12,
            pointerEvents: 'none',
          }}
        >
          ‖
        </div>

        {/* Transparent native range OVER the track — drag + keyboard + a11y for free. */}
        <input
          type="range"
          min={0}
          max={100}
          step={0.01}
          value={target}
          aria-label={direction === 'OVER' ? 'Roll over' : 'Roll under'}
          onChange={(e) => onTargetChange(Number(e.target.value))}
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: 28,
            margin: 0,
            opacity: 0,
            cursor: 'pointer',
          }}
        />
      </div>

      {/* Tick labels 0 / 25 / 50 / 75 / 100. */}
      <div
        aria-hidden
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 12,
          color: '#8b93a7',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {TICKS.map((t) => (
          <span key={t}>{t}</span>
        ))}
      </div>

      {/* The authoritative server roll as text — correct regardless of any animation. */}
      {hasRoll && (
        <p
          data-testid="dice-roll"
          aria-label="Roll"
          style={{
            margin: 0,
            textAlign: 'center',
            fontSize: 13,
            color: '#8b93a7',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          Roll{' '}
          <strong style={{ color: props.won ? GREEN : RED, fontSize: 16 }}>
            {(props.roll as number).toFixed(2)}
          </strong>
        </p>
      )}
    </div>
  );
}

// A single cosmetic pip that animates from the track origin to the server roll
// position. Reduced motion → starts already at the result (no tween). Re-mounted per
// bet (keyed by betId in the parent) so an identical roll still replays.
function ResultMarker(props: { landing: DiceResultLanding; won?: boolean }) {
  const { leftPct, immediate } = props.landing;
  const [pct, setPct] = useState(immediate ? leftPct : 0);

  useEffect(() => {
    if (immediate) return;
    const id = requestAnimationFrame(() => setPct(leftPct));
    return () => cancelAnimationFrame(id);
  }, [immediate, leftPct]);

  return (
    <div
      aria-hidden
      style={{
        position: 'absolute',
        top: -16,
        left: `${pct}%`,
        transform: 'translateX(-50%)',
        transition: immediate ? 'none' : 'left 600ms ease-out',
      }}
    >
      <div
        style={{
          width: 0,
          height: 0,
          margin: '0 auto',
          borderLeft: '5px solid transparent',
          borderRight: '5px solid transparent',
          borderTop: `7px solid ${props.won ? GREEN : RED}`,
        }}
      />
    </div>
  );
}
