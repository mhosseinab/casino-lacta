import { useEffect, useState } from 'react';
import { useReducedMotion } from '../../pixi/useReducedMotion';
import type { RouletteColour } from './rouletteMath';

// The Roulette "wheel" — the cosmetic spin display (the Gamdom-style scrolling row).
// It is purely COSMETIC: it scrolls and only resolves TO the SERVER result/colour;
// it decides nothing. Mirrors the S9 Dice / S11 Limbo reference pattern:
//   1. PURE, unit-tested landing math (rouletteLanding) — jsdom can't run a real
//      animation, so the "server result → displayed result" mapping is the tested seam.
//   2. Reduced motion → show the result IMMEDIATELY (no spin).
//   3. The spin is keyed on BET IDENTITY (betId) in the parent, so two identical
//      consecutive results still replay.

const COLOURS: Record<RouletteColour, string> = {
  GREEN: '#1bd96a',
  RED: '#e0364f',
  BLACK: '#9aa3b5',
};

export interface RouletteLanding {
  /** The server result, clamped into the 0–99 pocket domain. */
  result: number;
  /** The server-decided colour of that pocket. */
  colour: RouletteColour;
  /** True under reduced motion: show `result` immediately, no spin. */
  immediate: boolean;
}

/** Pure: maps a server result + colour to the spin's end state. The single owner of
 *  the value mapping; RouletteWheel renders THROUGH it. */
export function rouletteLanding(
  result: number,
  colour: RouletteColour,
  reducedMotion: boolean,
): RouletteLanding {
  return {
    result: Math.min(99, Math.max(0, Math.trunc(result))),
    colour,
    immediate: reducedMotion,
  };
}

export function RouletteWheel(props: {
  result?: number | null;
  colour?: RouletteColour | null;
  betId?: string | null;
}) {
  const reducedMotion = useReducedMotion();
  const hasResult =
    props.result != null &&
    Number.isFinite(props.result) &&
    props.colour != null;

  return (
    <div
      data-testid="roulette-stage"
      data-static={reducedMotion ? 'true' : 'false'}
      style={{
        display: 'grid',
        placeItems: 'center',
        gap: 14,
        padding: '32px 16px',
        minHeight: 220,
        background: '#0c1018',
        border: '1px solid #222a38',
        borderRadius: 12,
      }}
    >
      <span
        aria-hidden
        style={{
          fontSize: 13,
          letterSpacing: 2,
          fontWeight: 700,
          color: '#8b93a7',
          textTransform: 'uppercase',
        }}
      >
        Roulette
      </span>

      {hasResult ? (
        <Pocket
          key={props.betId ?? `${props.result}-${props.colour}`}
          landing={rouletteLanding(
            props.result as number,
            props.colour as RouletteColour,
            reducedMotion,
          )}
        />
      ) : (
        // Pre-bet placeholder.
        <span
          aria-hidden
          style={{
            fontSize: 64,
            fontWeight: 800,
            color: '#2b3346',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          ··
        </span>
      )}

      {/* Authoritative server value as text — correct regardless of the spin. */}
      {hasResult && (
        <p
          data-testid="roulette-result"
          aria-label="Spin result"
          style={{
            margin: 0,
            fontSize: 14,
            color: '#8b93a7',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          Landed on{' '}
          <strong
            style={{
              color: COLOURS[props.colour as RouletteColour],
              fontSize: 16,
            }}
          >
            {props.result} · {props.colour}
          </strong>
        </p>
      )}
    </div>
  );
}

// The big landed pocket. Fades/scales in to the result; under reduced motion it
// appears already resolved. Re-mounted per bet (keyed by betId in the parent) so an
// identical result still replays.
function Pocket(props: { landing: RouletteLanding }) {
  const { result, colour, immediate } = props.landing;
  const [shown, setShown] = useState(immediate);

  useEffect(() => {
    if (immediate) return;
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, [immediate]);

  return (
    <span
      aria-hidden
      style={{
        fontSize: 64,
        fontWeight: 800,
        color: COLOURS[colour],
        fontVariantNumeric: 'tabular-nums',
        lineHeight: 1,
        opacity: shown ? 1 : 0,
        transform: shown ? 'scale(1)' : 'scale(0.6)',
        transition: immediate
          ? 'none'
          : 'opacity 400ms ease-out, transform 400ms ease-out',
      }}
    >
      {result}
    </span>
  );
}
