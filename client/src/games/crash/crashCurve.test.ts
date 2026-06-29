import { describe, expect, it } from 'vitest';
import type {
  CrashCrashEvent,
  CrashRoundEvent,
  CrashTickEvent,
} from '../../lib/transport';
import {
  type CrashState,
  INITIAL_CRASH_STATE,
  backoffDelayMs,
  crashStateFromResume,
  curvePoints,
  reduceCrashEvent,
} from './crashCurve';

const roundEvt: CrashRoundEvent = {
  type: 'round',
  roundId: 'round-7',
  roundNumber: 7,
  serverSeedHash: 'hash-7',
  status: 'WAITING',
};
const tickEvt: CrashTickEvent = {
  type: 'tick',
  roundId: 'round-7',
  roundNumber: 7,
  multiplier: 1.42,
};
const crashEvt: CrashCrashEvent = {
  type: 'crash',
  roundId: 'round-7',
  roundNumber: 7,
  crashPoint: 1.87,
  serverSeed: 'seed-raw-7',
  serverSeedHash: 'hash-7',
  status: 'CRASHED',
};

describe('crashCurve — pure spectator state (server-authoritative)', () => {
  describe('reduceCrashEvent', () => {
    it('round → WAITING: commits the hash, resets the curve to 1.00×, hides seed/C', () => {
      const s = reduceCrashEvent(INITIAL_CRASH_STATE, roundEvt);
      expect(s.phase).toBe('WAITING');
      expect(s.roundId).toBe('round-7');
      expect(s.multiplier).toBe(1);
      expect(s.serverSeedHash).toBe('hash-7');
      expect(s.serverSeed).toBeNull();
      expect(s.crashPoint).toBeNull();
    });

    it('tick → RUNNING: reflects the SERVER multiplier verbatim (never computed)', () => {
      const s = reduceCrashEvent(
        reduceCrashEvent(INITIAL_CRASH_STATE, roundEvt),
        tickEvt,
      );
      expect(s.phase).toBe('RUNNING');
      expect(s.multiplier).toBe(1.42);
      // Still no reveal mid-round.
      expect(s.serverSeed).toBeNull();
      expect(s.crashPoint).toBeNull();
    });

    it('crash → CRASHED: reveals the server crash point C + the raw seed', () => {
      const running = reduceCrashEvent(
        reduceCrashEvent(INITIAL_CRASH_STATE, roundEvt),
        tickEvt,
      );
      const s = reduceCrashEvent(running, crashEvt);
      expect(s.phase).toBe('CRASHED');
      expect(s.crashPoint).toBe(1.87);
      expect(s.multiplier).toBe(1.87); // settles to C
      expect(s.serverSeed).toBe('seed-raw-7');
      expect(s.serverSeedHash).toBe('hash-7'); // the seed hashes back to this
    });
  });

  describe('crashStateFromResume — reconnect/resume of the shared round', () => {
    it('returns IDLE when no round exists yet (404 → {round:null})', () => {
      expect(crashStateFromResume({ round: null, bets: [] })).toEqual(
        INITIAL_CRASH_STATE,
      );
    });

    it('restores a live RUNNING round with the server multiplier, no seed/C', () => {
      const s = crashStateFromResume({
        round: {
          roundId: 'round-9',
          roundNumber: 9,
          serverSeedHash: 'hash-9',
          status: 'RUNNING',
          multiplier: 2.31,
        },
        bets: [],
      });
      expect(s.phase).toBe('RUNNING');
      expect(s.multiplier).toBe(2.31);
      expect(s.serverSeedHash).toBe('hash-9');
      expect(s.serverSeed).toBeNull();
      expect(s.crashPoint).toBeNull();
    });

    it('restores a SETTLED round with the revealed seed + crash point', () => {
      const s = crashStateFromResume({
        round: {
          roundId: 'round-9',
          roundNumber: 9,
          serverSeedHash: 'hash-9',
          status: 'SETTLED',
          multiplier: 3.5,
          serverSeed: 'seed-raw-9',
          crashPoint: 3.5,
        },
        bets: [],
      });
      expect(s.phase).toBe('CRASHED');
      expect(s.crashPoint).toBe(3.5);
      expect(s.serverSeed).toBe('seed-raw-9');
    });
  });

  describe('backoffDelayMs — deterministic reconnect backoff (no RNG)', () => {
    it('grows exponentially from 500ms and caps at 30s', () => {
      expect(backoffDelayMs(0)).toBe(500);
      expect(backoffDelayMs(1)).toBe(1000);
      expect(backoffDelayMs(2)).toBe(2000);
      expect(backoffDelayMs(20)).toBe(30_000); // capped
    });
  });

  describe('curvePoints — cosmetic SVG geometry', () => {
    it('produces one point per tick, rising (higher multiplier → smaller y)', () => {
      const pts = curvePoints([1, 1.5, 2], 100, 50).split(' ');
      expect(pts).toHaveLength(3);
      const ys = pts.map((p) => Number(p.split(',')[1]));
      expect(ys[0]).toBeGreaterThan(ys[2]); // the curve climbs upward
    });

    it('is empty for no ticks', () => {
      expect(curvePoints([], 100, 50)).toBe('');
    });
  });
});

// Type-only guard: CrashState shape is exercised above; reference it so an unused
// import can't mask a contract drift.
const _typeGuard: CrashState = INITIAL_CRASH_STATE;
void _typeGuard;
