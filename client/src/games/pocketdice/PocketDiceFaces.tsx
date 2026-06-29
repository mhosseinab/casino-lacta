import { useReducedMotion } from '../../pixi/useReducedMotion';

// The Pocket Dice "faces" — the two server-rolled dice + their sum. Purely COSMETIC:
// it renders the SERVER outcome (dice/sum) and decides nothing. Mirrors the S9/S11
// reference pattern:
//   1. PURE, unit-tested landing math (diceFacesLanding) — jsdom can't run a roll
//      animation, so the "server dice → displayed faces + sum" mapping is the tested seam.
//   2. Reduced motion → show the result IMMEDIATELY (no roll animation).
//   3. The roll-in is keyed on BET IDENTITY (betId) in the parent, so two identical
//      consecutive results still replay.
// The authoritative caption shows the SERVER sum + Won/Lost regardless of any animation.

const GREEN = '#1bd96a';
const RED = '#e0364f';

export interface DiceFacesLanding {
  /** The two server dice faces to display. */
  faces: [number, number];
  /** Their sum (the authoritative outcome value). */
  sum: number;
  /** True under reduced motion: show immediately, no roll animation. */
  immediate: boolean;
}

/** Pure: maps the server dice to the faces display. The single owner of the mapping;
 *  PocketDiceFaces renders THROUGH it. */
export function diceFacesLanding(
  dice: [number, number],
  reducedMotion: boolean,
): DiceFacesLanding {
  return { faces: dice, sum: dice[0] + dice[1], immediate: reducedMotion };
}

// Standard die pip layout per face value (1–6), as a 3×3 grid of filled cells.
const PIP_GRID: Readonly<Record<number, ReadonlyArray<number>>> = {
  1: [4],
  2: [0, 8],
  3: [0, 4, 8],
  4: [0, 2, 6, 8],
  5: [0, 2, 4, 6, 8],
  6: [0, 2, 3, 5, 6, 8],
};
// The nine grid cell ids (0–8). A constant so each cell keys on its own stable id,
// not the map index — the grid is fixed and never reorders.
const PIP_CELLS = [0, 1, 2, 3, 4, 5, 6, 7, 8] as const;

export function PocketDiceFaces(props: {
  dice?: [number, number] | null;
  sum?: number | null;
  target: number;
  direction: 'UNDER' | 'OVER';
  betId?: string | null;
  won?: boolean;
}) {
  const reducedMotion = useReducedMotion();
  const hasResult =
    props.dice != null &&
    props.sum != null &&
    Number.isFinite(props.dice[0]) &&
    Number.isFinite(props.dice[1]);
  const color = props.won ? GREEN : RED;
  const landing = hasResult
    ? diceFacesLanding(props.dice as [number, number], reducedMotion)
    : null;

  return (
    <div
      data-testid="pocketdice-faces"
      data-static={reducedMotion ? 'true' : 'false'}
      style={{
        display: 'grid',
        placeItems: 'center',
        gap: 16,
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
        Pocket Dice
      </span>

      {landing ? (
        <div
          key={props.betId ?? landing.sum}
          style={{ display: 'flex', gap: 20 }}
        >
          {landing.faces.map((face, i) => (
            <Die
              // Two dice positionally indexed; stable within one settled result.
              key={`die-${i}-${face}`}
              face={face}
              label={`Die ${i + 1} showing ${face}`}
              color={color}
              immediate={landing.immediate}
            />
          ))}
        </div>
      ) : (
        // Pre-bet placeholder: the chosen target as a faded preview.
        <span
          aria-hidden
          style={{
            fontSize: 56,
            fontWeight: 800,
            color: '#2b3346',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {props.direction === 'UNDER' ? '<' : '>'} {props.target}
        </span>
      )}

      {/* Authoritative server sum + result — correct regardless of any animation. */}
      {hasResult && (
        <p
          data-testid="pocketdice-result"
          aria-label="Dice sum"
          style={{
            margin: 0,
            fontSize: 14,
            color: '#8b93a7',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {props.won ? 'Won' : 'Lost'} — rolled{' '}
          <strong style={{ color, fontSize: 16 }}>{props.sum}</strong>
        </p>
      )}
    </div>
  );
}

// A single cosmetic die face: a rounded tile with the standard pip layout. Under
// reduced motion it appears at rest; otherwise it scales/fades in (keyed per bet in
// the parent so an identical roll still replays).
function Die(props: {
  face: number;
  label: string;
  color: string;
  immediate: boolean;
}) {
  const pips = PIP_GRID[props.face] ?? [];
  return (
    <div
      aria-label={props.label}
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gridTemplateRows: 'repeat(3, 1fr)',
        gap: 4,
        width: 64,
        height: 64,
        padding: 8,
        borderRadius: 12,
        background: '#f4f6fb',
        boxShadow: `0 0 0 2px ${props.color}, 0 4px 10px rgba(0,0,0,0.45)`,
        animation: props.immediate
          ? undefined
          : 'pocketdice-roll-in 360ms ease-out',
      }}
    >
      {PIP_CELLS.map((cell) => (
        <span
          key={cell}
          aria-hidden
          style={{
            borderRadius: '50%',
            background: pips.includes(cell) ? '#1b2230' : 'transparent',
          }}
        />
      ))}
    </div>
  );
}
