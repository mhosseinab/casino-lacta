// Pure, presentation-only Crash helpers — the testable seam of the realtime view.
//
// Crash is SERVER-AUTHORITATIVE and MULTIPLAYER: one shared round's curve rises and
// "crashes" at a point C the server fixed at round open from a committed seed. This
// client view is a pure SPECTATOR — it reflects the server's `round`/`tick`/`crash`
// events VERBATIM and decides NOTHING. The cosmetic multiplier shown is the server's
// last-published tick; the authoritative crash point C and the raw seed are revealed
// by the server only in the `crash` event (the seed hashes back to the committed
// `serverSeedHash`). Nothing here computes a multiplier, a crash point, or money.
import type { CrashEvent } from '../../lib/transport';

export type CrashPhase = 'IDLE' | 'WAITING' | 'RUNNING' | 'CRASHED';

export interface CrashState {
  phase: CrashPhase;
  roundId: string | null;
  roundNumber: number | null;
  /** The server's last-published cosmetic multiplier (tick), or C once CRASHED. */
  multiplier: number;
  /** The round's commitment, shown live; the raw seed hashes to this at CRASHED. */
  serverSeedHash: string | null;
  /** Revealed ONLY at CRASHED (hashes, SHA-256, back to serverSeedHash). */
  serverSeed: string | null;
  /** The authoritative crash point C — revealed by the server at CRASHED. */
  crashPoint: number | null;
}

export const INITIAL_CRASH_STATE: CrashState = {
  phase: 'IDLE',
  roundId: null,
  roundNumber: null,
  multiplier: 1,
  serverSeedHash: null,
  serverSeed: null,
  crashPoint: null,
};

/** Pure: fold one server event into the view state. The client NEVER decides the
 *  multiplier or crash point — it only mirrors what the server published. */
export function reduceCrashEvent(
  state: CrashState,
  event: CrashEvent,
): CrashState {
  switch (event.type) {
    case 'round':
      // A new round opened (WAITING): reset the curve to 1.00×, commit the hash.
      return {
        phase: 'WAITING',
        roundId: event.roundId,
        roundNumber: event.roundNumber,
        multiplier: 1,
        serverSeedHash: event.serverSeedHash,
        serverSeed: null,
        crashPoint: null,
      };
    case 'tick':
      // RUNNING: reflect the server's last-published cosmetic multiplier verbatim.
      return {
        ...state,
        phase: 'RUNNING',
        roundId: event.roundId,
        roundNumber: event.roundNumber,
        multiplier: event.multiplier,
      };
    case 'crash':
      // CRASHED: the server reveals C + the raw seed (it hashes to the commitment).
      return {
        ...state,
        phase: 'CRASHED',
        roundId: event.roundId,
        roundNumber: event.roundNumber,
        multiplier: event.crashPoint,
        crashPoint: event.crashPoint,
        serverSeed: event.serverSeed,
        serverSeedHash: event.serverSeedHash,
      };
    default:
      return state;
  }
}

/** Pure: seed the view from the `GET /games/originals.crash/state` resume projection
 *  (the current shared round; the seed/C only appear once the round is SETTLED). */
export function crashStateFromResume(resume: {
  [key: string]: unknown;
}): CrashState {
  const round = resume.round as Record<string, unknown> | null | undefined;
  if (round == null) return INITIAL_CRASH_STATE;
  const status = String(round.status ?? '');
  const crashPoint =
    typeof round.crashPoint === 'number' ? round.crashPoint : null;
  const settled = status === 'SETTLED' || crashPoint != null;
  const phase: CrashPhase = settled
    ? 'CRASHED'
    : status === 'RUNNING' || status === 'LOCKED'
      ? 'RUNNING'
      : status === 'WAITING'
        ? 'WAITING'
        : 'IDLE';
  const live = typeof round.multiplier === 'number' ? round.multiplier : 1;
  return {
    phase,
    roundId: typeof round.roundId === 'string' ? round.roundId : null,
    roundNumber:
      typeof round.roundNumber === 'number' ? round.roundNumber : null,
    multiplier: crashPoint ?? live,
    serverSeedHash:
      typeof round.serverSeedHash === 'string' ? round.serverSeedHash : null,
    serverSeed: typeof round.serverSeed === 'string' ? round.serverSeed : null,
    crashPoint,
  };
}

const BACKOFF_BASE_MS = 500;
const BACKOFF_MAX_MS = 30_000;

/** Pure: deterministic exponential reconnect backoff (no jitter/RNG — the Crash
 *  view stays RNG-free). attempt 0 → 500ms, 1 → 1000ms, … capped at 30s. */
export function backoffDelayMs(attempt: number): number {
  const exp = BACKOFF_BASE_MS * 2 ** Math.max(0, attempt);
  return Math.min(BACKOFF_MAX_MS, exp);
}

/** Pure: map a tick sequence to SVG polyline points within (width, height). X is
 *  evenly spaced by tick index; Y maps [1, peak] → [height, 0] so the curve rises. */
export function curvePoints(
  multipliers: number[],
  width: number,
  height: number,
): string {
  if (multipliers.length === 0) return '';
  const peak = Math.max(...multipliers, 1.0000001); // avoid /0 when flat at 1.00
  const n = multipliers.length;
  return multipliers
    .map((m, i) => {
      const x = n === 1 ? 0 : (i / (n - 1)) * width;
      const y = height - ((m - 1) / (peak - 1)) * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');
}
