import { useEffect, useRef, useState } from 'react';
import type {
  CrashSubscription,
  GameStateProjection,
} from '../../lib/transport';
import { useGameClient } from '../../lib/transport';
import { CrashStage } from './CrashStage';
import {
  type CrashState,
  INITIAL_CRASH_STATE,
  backoffDelayMs,
  crashStateFromResume,
  reduceCrashEvent,
} from './crashCurve';

// CrashView — the realtime, MULTIPLAYER Crash Original (spec §A.4) as a SPECTATOR.
// It subscribes to the shared round/tick/crash stream via the GameClient seam,
// resumes the current round on mount via /state, and reconnects with backoff. It is
// a pure THIN renderer: the cosmetic curve mirrors the server's ticks, and the
// authoritative crash point C + the raw seed are shown only when the SERVER reveals
// them at CRASHED (the seed hashes back to the committed serverSeedHash). It decides
// no multiplier, no crash point, and no balance — and there is NO client RNG.
//
// NOTE (scope): placing a bet + cashing out are deliberately NOT here. The server
// exposes Crash as a read-only fan-out (no client-facing place-bet / cash-out
// endpoint, and the GameClient seam has no crash bet/cash-out method), so betting is
// deferred until the backend wires it. This view is the live spectator slice.
const GAME_ID = 'originals.crash';

const BG = '#0f1320';
const PANEL = '#171c28';
const FIELD = '#0c1018';
const BORDER = '#222a38';
const GREEN = '#1bd96a';
const RED = '#e0364f';
const TEXT = '#e6e9ef';
const MUTED = '#8b93a7';

function phaseLabel(state: CrashState): string {
  switch (state.phase) {
    case 'WAITING':
      return 'Next round starting…';
    case 'RUNNING':
      return 'Round in progress';
    case 'CRASHED':
      return state.crashPoint != null
        ? `Crashed at ${state.crashPoint.toFixed(2)}×`
        : 'Crashed';
    default:
      return 'Connecting to the live round…';
  }
}

export default function CrashView() {
  const client = useGameClient();
  const [state, setState] = useState<CrashState>(INITIAL_CRASH_STATE);
  const [multipliers, setMultipliers] = useState<number[]>([1]);
  const [connected, setConnected] = useState(false);

  // Resume the shared round on mount (a 404 = no round yet → stay IDLE).
  useEffect(() => {
    let cancelled = false;
    client
      .state(GAME_ID)
      .then((resume: GameStateProjection) => {
        if (cancelled) return;
        const restored = crashStateFromResume(resume);
        setState(restored);
        setMultipliers([restored.multiplier]);
      })
      .catch(() => {
        /* no round has opened yet — remain IDLE until the first event */
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  // Subscribe to the live stream with reconnect backoff. Mutable connection state
  // lives in refs so the effect runs once and the handlers never capture stale data.
  const closedRef = useRef(false);
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const subRef = useRef<CrashSubscription | null>(null);

  useEffect(() => {
    closedRef.current = false;
    attemptRef.current = 0;

    const scheduleReconnect = (): void => {
      if (closedRef.current) return;
      // Coalesce: a browser fires `error` THEN `close` for an errored socket, so
      // both handlers call this for ONE disconnect. Without this guard that would
      // schedule two reconnects → two live sockets (one leaked) + doubled events.
      if (timerRef.current != null) return;
      const delay = backoffDelayMs(attemptRef.current);
      attemptRef.current += 1;
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        connect();
      }, delay);
    };

    function connect(): void {
      if (closedRef.current) return;
      subRef.current = client.crashSocket({
        onOpen: () => {
          if (closedRef.current) return;
          attemptRef.current = 0; // reset backoff on a clean connection
          setConnected(true);
        },
        onEvent: (event) => {
          if (closedRef.current) return;
          setState((prev) => reduceCrashEvent(prev, event));
          if (event.type === 'round') setMultipliers([1]);
          else if (event.type === 'tick')
            setMultipliers((ms) => [...ms, event.multiplier]);
          else if (event.type === 'crash')
            setMultipliers((ms) => [...ms, event.crashPoint]);
        },
        onClose: () => {
          if (closedRef.current) return;
          setConnected(false);
          scheduleReconnect();
        },
        onError: () => {
          if (closedRef.current) return;
          setConnected(false);
          scheduleReconnect();
        },
      });
    }

    connect();

    return () => {
      closedRef.current = true;
      if (timerRef.current != null) clearTimeout(timerRef.current);
      subRef.current?.close();
    };
  }, [client]);

  const revealed = state.phase === 'CRASHED' && state.serverSeed != null;
  const bannerColor =
    state.phase === 'CRASHED' ? RED : state.phase === 'RUNNING' ? GREEN : MUTED;

  return (
    <section
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(260px, 320px) 1fr',
        gap: 2,
        margin: 24,
        borderRadius: 12,
        overflow: 'hidden',
        border: `1px solid ${BORDER}`,
        background: BORDER,
        color: TEXT,
        fontFamily: 'inherit',
      }}
    >
      {/* ---- Left info panel ----------------------------------------------- */}
      <div style={{ background: PANEL, padding: 16, display: 'grid', gap: 14 }}>
        <div style={{ display: 'grid', gap: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
            Crash — live shared round
          </span>
          <span
            data-testid="crash-connection"
            data-connected={connected ? 'true' : 'false'}
            style={{ fontSize: 12, color: connected ? GREEN : MUTED }}
          >
            {connected ? '● Connected' : '○ Reconnecting…'}
          </span>
        </div>

        {/* Spectator notice — betting/cash-out are server-side TBD (see file note). */}
        <p
          data-testid="crash-spectator-note"
          style={{
            margin: 0,
            padding: 12,
            borderRadius: 8,
            background: FIELD,
            border: `1px solid ${BORDER}`,
            color: MUTED,
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          You're watching the live, provably-fair shared round. Play-money
          betting and cash-out open in a later release.
        </p>

        {/* Provable fairness for the round (per-round seed, §A.4). */}
        <div style={{ display: 'grid', gap: 6 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
            Provably fair (per round)
          </span>
          <div style={{ display: 'grid', gap: 4 }}>
            <span style={{ fontSize: 11, color: MUTED }}>Server seed hash</span>
            <code
              data-testid="crash-seed-hash"
              style={{
                ...codeStyle,
                color: state.serverSeedHash ? TEXT : MUTED,
              }}
            >
              {state.serverSeedHash ?? '—'}
            </code>
          </div>
          {revealed && (
            <div style={{ display: 'grid', gap: 4 }}>
              <span style={{ fontSize: 11, color: MUTED }}>
                Server seed (revealed)
              </span>
              <code data-testid="crash-seed-revealed" style={codeStyle}>
                {state.serverSeed}
              </code>
              <span style={{ fontSize: 11, color: MUTED }}>
                This seed (SHA-256) hashes back to the committed hash above —
                verify the round in the open verifier.
              </span>
            </div>
          )}
        </div>
      </div>

      {/* ---- Right stage --------------------------------------------------- */}
      <div
        style={{
          background: BG,
          padding: 24,
          display: 'grid',
          gap: 20,
          alignContent: 'start',
        }}
      >
        <div style={{ display: 'grid', gap: 6, justifyItems: 'center' }}>
          <span
            data-testid="crash-phase"
            style={{ fontSize: 14, fontWeight: 700, color: bannerColor }}
          >
            {phaseLabel(state)}
          </span>
          {state.roundNumber != null && (
            <span style={{ fontSize: 12, color: MUTED }}>
              Round #{state.roundNumber}
            </span>
          )}
        </div>

        <CrashStage
          phase={state.phase}
          multiplier={state.multiplier}
          multipliers={multipliers}
        />
      </div>
    </section>
  );
}

const codeStyle: React.CSSProperties = {
  font: '12px ui-monospace, monospace',
  padding: '6px 8px',
  borderRadius: 6,
  border: `1px solid ${BORDER}`,
  background: FIELD,
  color: TEXT,
  wordBreak: 'break-all',
};
