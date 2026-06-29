import { useEffect, useState } from 'react';
import { useReducedMotion } from '../../pixi/useReducedMotion';
import {
  type Bounce,
  binCount,
  binLeftPct,
  columnToLeftPct,
  pathToColumns,
} from './plinkoMath';

// The Plinko board — a purely COSMETIC renderer of a server-decided drop (mirrors the
// S9 DiceSlider reference). It decides NOTHING:
//   • The ball lands at the AUTHORITATIVE `bin` (server `outcome.bin`), positioned via
//     the shared `binLeftPct` mapping — never re-derived from `path`.
//   • `path` is the (proven-consistent, see plinkoMath) cosmetic trail only.
//   • Reduced motion → the ball appears in the final bin IMMEDIATELY (no drop).
//   • The drop is re-mounted per bet (keyed by betId in the parent) so an identical
//     consecutive bin still replays — CARRY-FORWARD: animate on bet IDENTITY, not value.
// The authoritative landed-bin text is always rendered, independent of any animation,
// so tests (and screen readers) never depend on rAF timing.

const GREEN = '#1bd96a';
const RED = '#e0364f';

export function PlinkoBoard(props: {
  rows: number;
  path?: Bounce[] | null;
  bin?: number | null;
  betId?: string | null;
  won?: boolean;
}) {
  const reducedMotion = useReducedMotion();
  const { rows } = props;
  const hasResult =
    props.path != null && props.bin != null && Number.isFinite(props.bin);
  const bins = binCount(rows);

  return (
    <div
      data-testid="plinko-board"
      data-static={reducedMotion ? 'true' : 'false'}
      style={{
        position: 'relative',
        display: 'grid',
        gap: 16,
        padding: 24,
        minHeight: 320,
        background: '#0c1018',
        border: '1px solid #222a38',
        borderRadius: 12,
      }}
    >
      {/* Peg field (cosmetic). Each row has one more peg than the row above it. */}
      <div style={{ position: 'relative', display: 'grid', gap: 14 }}>
        {Array.from({ length: rows }, (_, r) => (
          <div
            // biome-ignore lint/suspicious/noArrayIndexKey: static cosmetic grid
            key={r}
            style={{
              display: 'flex',
              justifyContent: 'center',
              gap: 18,
            }}
          >
            {Array.from({ length: r + 2 }, (_, p) => (
              <span
                // biome-ignore lint/suspicious/noArrayIndexKey: static cosmetic grid
                key={p}
                aria-hidden
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: 999,
                  background: '#3a4456',
                }}
              />
            ))}
          </div>
        ))}

        {/* The cosmetic ball — lands at the authoritative server bin. */}
        {hasResult && (
          <PlinkoDrop
            key={props.betId ?? `${props.bin}`}
            rows={rows}
            path={props.path as Bounce[]}
            bin={props.bin as number}
            reducedMotion={reducedMotion}
            won={props.won}
          />
        )}
      </div>

      {/* Bin row — rows+1 slots sharing the ball's horizontal mapping. */}
      <div style={{ position: 'relative', height: 26 }}>
        {Array.from({ length: bins }, (_, k) => (
          <span
            // biome-ignore lint/suspicious/noArrayIndexKey: static cosmetic grid
            key={k}
            data-testid="plinko-bin"
            aria-hidden
            style={{
              position: 'absolute',
              left: `${binLeftPct(k, rows)}%`,
              transform: 'translateX(-50%)',
              top: 0,
              minWidth: 18,
              padding: '4px 2px',
              textAlign: 'center',
              fontSize: 10,
              borderRadius: 4,
              background: hasResult && k === props.bin ? GREEN : '#1b2230',
              color: hasResult && k === props.bin ? '#06210f' : '#8b93a7',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {k}
          </span>
        ))}
      </div>

      {/* Authoritative landed-bin text — correct regardless of any animation. */}
      {hasResult && (
        <p
          data-testid="plinko-result"
          aria-label="Landed bin"
          style={{
            margin: 0,
            textAlign: 'center',
            fontSize: 13,
            color: '#8b93a7',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          Landed in bin{' '}
          <strong style={{ color: props.won ? GREEN : RED, fontSize: 16 }}>
            {props.bin}
          </strong>
        </p>
      )}
    </div>
  );
}

// A single cosmetic ball that drops from the top to the server bin. Reduced motion →
// it starts already at the bin (no drop). Re-mounted per bet (keyed by betId in the
// parent) so an identical consecutive bin still replays the drop.
function PlinkoDrop(props: {
  rows: number;
  path: Bounce[];
  bin: number;
  reducedMotion: boolean;
  won?: boolean;
}) {
  const targetPct = binLeftPct(props.bin, props.rows);
  // The cosmetic trail: each server bounce as a faint waypoint. It ends at the same
  // `targetPct` the ball lands on (columnToLeftPct of the final offset === binLeftPct),
  // so the drop visibly follows the server path into the authoritative bin.
  const columns = pathToColumns(props.path);

  const [dropped, setDropped] = useState(props.reducedMotion);

  useEffect(() => {
    if (props.reducedMotion) return;
    const id = requestAnimationFrame(() => setDropped(true));
    return () => cancelAnimationFrame(id);
  }, [props.reducedMotion]);

  return (
    <>
      {/* Faint waypoints tracing the server path through the peg field (cosmetic). */}
      {!props.reducedMotion &&
        columns.map((column, i) => (
          <span
            // biome-ignore lint/suspicious/noArrayIndexKey: positional trail markers
            key={i}
            aria-hidden
            style={{
              position: 'absolute',
              left: `${columnToLeftPct(column, props.rows)}%`,
              top: `${((i + 1) / props.rows) * 100}%`,
              transform: 'translate(-50%, -50%)',
              width: 4,
              height: 4,
              borderRadius: 999,
              background: 'rgba(27,217,106,0.25)',
            }}
          />
        ))}

      <div
        data-testid="plinko-ball"
        data-bin={props.bin}
        aria-hidden
        style={{
          position: 'absolute',
          left: `${targetPct}%`,
          top: dropped ? '100%' : '0%',
          transform: 'translate(-50%, -50%)',
          width: 12,
          height: 12,
          borderRadius: 999,
          background: props.won ? GREEN : RED,
          boxShadow: '0 0 8px rgba(0,0,0,0.5)',
          transition: props.reducedMotion ? 'none' : 'top 700ms ease-in',
        }}
      />
    </>
  );
}
