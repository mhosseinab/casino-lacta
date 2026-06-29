import { useEffect, useState } from 'react';
import { useReducedMotion } from '../../pixi/useReducedMotion';
import { KENO_GRID_MAX, KENO_GRID_MIN } from './kenoMath';

// The Keno number grid (1..40) — DOUBLE duty, kept strictly separate:
//   • Before a draw it is the player's INPUT: tapping a cell toggles a pick. It
//     decides nothing; the server draws and pays.
//   • On a result it COSMETICALLY reveals the SERVER `drawn` set, lighting the
//     hit/drawn cells. It only resolves TO the server values — never derives them.
//
// Reference discipline carried from S9 (DiceSlider):
//   1. PURE, unit-tested mapping (kenoReveal / cellState) — jsdom can't run a real
//      reveal, so the render goes THROUGH these tested functions.
//   2. Reduced motion → reveal the whole draw IMMEDIATELY (no stagger).
//   3. Re-animation is keyed on BET IDENTITY (the parent remounts via betId), so two
//      consecutive results still replay the reveal from empty.

const IDLE = '#1b2230';
const PICK = '#1f6feb';
const HIT = '#1bd96a';
const DRAWN = '#3a4456';
const BORDER = '#222a38';

export type KenoCellState = 'idle' | 'pick' | 'hit' | 'drawn';

export interface KenoReveal {
  /** The server drawn set to reveal (verbatim). */
  drawn: number[];
  /** True under reduced motion: show the whole draw immediately, no stagger. */
  immediate: boolean;
}

/** Pure: maps the server drawn set to the reveal descriptor. The single owner of the
 *  "drawn → reveal" mapping; KenoGrid renders THROUGH it. */
export function kenoReveal(
  drawn: number[],
  reducedMotion: boolean,
): KenoReveal {
  return { drawn, immediate: reducedMotion };
}

/** Pure: the cosmetic appearance of one grid number given the current picks and the
 *  (already-revealed) drawn set, or `null` before a draw. Decides no outcome. */
export function cellState(
  n: number,
  picks: number[],
  drawn: number[] | null,
): KenoCellState {
  const isPick = picks.includes(n);
  const isDrawn = drawn?.includes(n) ?? false;
  if (isPick && isDrawn) return 'hit';
  if (isPick) return 'pick';
  if (isDrawn) return 'drawn';
  return 'idle';
}

const REVEAL_STEP_MS = 90;

export function KenoGrid(props: {
  picks: number[];
  drawn: number[] | null;
  betId?: string | null;
  onToggle: (n: number) => void;
}) {
  const reducedMotion = useReducedMotion();
  const { drawn, immediate } = kenoReveal(props.drawn ?? [], reducedMotion);

  // Cosmetic stagger: how many of the drawn numbers are currently lit. Under reduced
  // motion (or no draw) the whole set is revealed at once. Keyed by betId in the
  // parent → this state resets and the reveal replays on every new result.
  const [revealedCount, setRevealedCount] = useState(
    immediate ? drawn.length : 0,
  );

  useEffect(() => {
    if (immediate || drawn.length === 0) {
      setRevealedCount(drawn.length);
      return;
    }
    setRevealedCount(0);
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setRevealedCount(i);
      if (i >= drawn.length) clearInterval(id);
    }, REVEAL_STEP_MS);
    return () => clearInterval(id);
  }, [immediate, drawn.length]);

  const visibleDrawn =
    props.drawn === null ? null : drawn.slice(0, revealedCount);

  const numbers: number[] = [];
  for (let n = KENO_GRID_MIN; n <= KENO_GRID_MAX; n += 1) numbers.push(n);

  return (
    <div
      data-testid="keno-grid"
      data-static={reducedMotion ? 'true' : 'false'}
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(8, 1fr)',
        gap: 8,
      }}
    >
      {numbers.map((n) => {
        const state = cellState(n, props.picks, visibleDrawn);
        const picked = props.picks.includes(n);
        return (
          <button
            key={n}
            type="button"
            aria-pressed={picked}
            onClick={() => props.onToggle(n)}
            style={cellStyle(state)}
          >
            {n}
          </button>
        );
      })}
    </div>
  );
}

function cellStyle(state: KenoCellState): React.CSSProperties {
  const background =
    state === 'hit'
      ? HIT
      : state === 'pick'
        ? PICK
        : state === 'drawn'
          ? DRAWN
          : IDLE;
  const color = state === 'hit' ? '#06210f' : '#e6e9ef';
  return {
    font: 'inherit',
    fontWeight: 700,
    padding: '14px 0',
    borderRadius: 8,
    border: `1px solid ${BORDER}`,
    background,
    color,
    cursor: 'pointer',
    fontVariantNumeric: 'tabular-nums',
  };
}
