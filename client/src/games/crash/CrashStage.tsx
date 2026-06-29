import { useReducedMotion } from '../../pixi/useReducedMotion';
import { type CrashPhase, curvePoints } from './crashCurve';

// CrashStage — the COSMETIC multiplier curve + the big live number. It renders the
// server's published ticks (an SVG polyline) and the current multiplier; it decides
// nothing. The curve is keyed on roundId by the parent so each round redraws fresh.
// Reduced motion just drops the CSS transition — the data is discrete server ticks,
// not a client animation, so there is no animation loop to gate.
const GREEN = '#1bd96a';
const RED = '#e0364f';
const MUTED = '#8b93a7';

const W = 320;
const H = 160;

function phaseColor(phase: CrashPhase): string {
  if (phase === 'CRASHED') return RED;
  if (phase === 'RUNNING') return GREEN;
  return MUTED;
}

export function CrashStage(props: {
  phase: CrashPhase;
  multiplier: number;
  multipliers: number[];
}) {
  const reducedMotion = useReducedMotion();
  const color = phaseColor(props.phase);
  const points = curvePoints(props.multipliers, W, H);

  return (
    <div
      data-testid="crash-stage"
      data-static={reducedMotion ? 'true' : 'false'}
      style={{
        position: 'relative',
        display: 'grid',
        placeItems: 'center',
        background: '#0c1018',
        border: '1px solid #222a38',
        borderRadius: 12,
        padding: 16,
        minHeight: H + 48,
        overflow: 'hidden',
      }}
    >
      <svg
        aria-hidden
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ position: 'absolute', inset: 16, width: 'auto', opacity: 0.5 }}
      >
        <title>Crash multiplier curve</title>
        {points !== '' && (
          <polyline
            points={points}
            fill="none"
            stroke={color}
            strokeWidth={3}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}
      </svg>
      <span
        data-testid="crash-multiplier"
        style={{
          position: 'relative',
          fontSize: 64,
          fontWeight: 800,
          color,
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1,
        }}
      >
        {props.multiplier.toFixed(2)}×
      </span>
    </div>
  );
}
