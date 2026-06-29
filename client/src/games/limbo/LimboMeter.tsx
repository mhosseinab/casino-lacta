import { useEffect, useState } from 'react';
import { useReducedMotion } from '../../pixi/useReducedMotion';

// The Limbo "meter" — the big centre multiplier display (the Gamdom-style number).
// It is purely COSMETIC: it counts up to the SERVER-generated multiplier and only
// resolves TO it; it decides nothing. Mirrors the S9 Dice reference pattern:
//   1. PURE, unit-tested landing math (limboLanding) — jsdom can't run a real
//      animation, so the "server value → displayed value" mapping is the tested seam.
//   2. Reduced motion → show the result IMMEDIATELY (no count-up).
//   3. The count-up is keyed on BET IDENTITY (betId) in the parent, so two
//      identical consecutive results still replay from the floor.

const GREEN = '#1bd96a';
const RED = '#e0364f';

export interface LimboLanding {
  /** The generated multiplier to display, floored at 1.00× (the §2.4 curve floor). */
  value: number;
  /** True under reduced motion: show `value` immediately, no count-up. */
  immediate: boolean;
}

/** Pure: maps a server-generated multiplier to the meter's end value. The single
 *  owner of the value mapping; LimboMeter renders THROUGH it. */
export function limboLanding(
  generated: number,
  reducedMotion: boolean,
): LimboLanding {
  return { value: Math.max(1, generated), immediate: reducedMotion };
}

export function LimboMeter(props: {
  target: number;
  generated?: number | null;
  betId?: string | null;
  won?: boolean;
}) {
  const reducedMotion = useReducedMotion();
  const hasResult = props.generated != null && Number.isFinite(props.generated);
  const color = props.won ? GREEN : RED;

  return (
    <div
      data-testid="limbo-meter"
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
        Limbo
      </span>

      {hasResult ? (
        <CountUp
          key={props.betId ?? String(props.generated)}
          landing={limboLanding(props.generated as number, reducedMotion)}
          color={color}
        />
      ) : (
        // Pre-bet placeholder: a faded preview of the chosen target.
        <span
          aria-hidden
          style={{
            fontSize: 64,
            fontWeight: 800,
            color: '#2b3346',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {props.target.toFixed(2)}×
        </span>
      )}

      {/* Authoritative server value as text — correct regardless of the count-up. */}
      {hasResult && (
        <p
          data-testid="limbo-result"
          aria-label="Generated multiplier"
          style={{
            margin: 0,
            fontSize: 14,
            color: '#8b93a7',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {props.won ? 'Won at' : 'Crashed at'}{' '}
          <strong style={{ color, fontSize: 16 }}>
            {(props.generated as number).toFixed(2)}×
          </strong>
        </p>
      )}
    </div>
  );
}

// The big count-up number. Animates from the 1.00× floor to the landing value via
// requestAnimationFrame; under reduced motion it starts already at the result.
// Re-mounted per bet (keyed by betId in the parent) so an identical result replays.
function CountUp(props: { landing: LimboLanding; color: string }) {
  const { value, immediate } = props.landing;
  const [shown, setShown] = useState(immediate ? value : 1);

  useEffect(() => {
    if (immediate) return;
    let raf = 0;
    let startTs = 0;
    const DURATION = 700;
    const tick = (ts: number) => {
      if (startTs === 0) startTs = ts;
      const t = Math.min(1, (ts - startTs) / DURATION);
      const eased = 1 - (1 - t) ** 3; // ease-out cubic
      setShown(1 + (value - 1) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [immediate, value]);

  return (
    <span
      aria-hidden
      style={{
        fontSize: 64,
        fontWeight: 800,
        color: props.color,
        fontVariantNumeric: 'tabular-nums',
        lineHeight: 1,
      }}
    >
      {shown.toFixed(2)}×
    </span>
  );
}
