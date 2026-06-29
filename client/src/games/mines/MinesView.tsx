import { useState } from 'react';
import { BetControls } from '../../components/BetControls';
import { formatMinor } from '../../lib/money';
import { useSession } from '../../lib/session';
import { BetRejectedError, useGameClient } from '../../lib/transport';
import { GRID_COLS, canCashout, gridCells, isMine } from './minesMath';

// MinesView — the FIRST STATEFUL game view, server-authoritative thin renderer.
// It OPENS a round (POST /bet, input {mines}), sends per-cell reveals + a cashout
// (POST /action), and renders the SERVER's projections VERBATIM. It decides no
// outcome, RNG, payout, or balance: the mine layout is committed server-side at
// open, every multiplier comes off the projection, and the balance is RE-FETCHED
// from /me after each money event (open debit, terminal credit/loss).
//
// The one piece of LOCAL state is presentation-only: /action's safe projection
// returns a single revealed `cell` (not the whole set), so we accumulate revealed
// cells into a Set purely to paint the board — never to decide anything.
const GAME_ID = 'originals.mines';

const MIN_MINES = 1;
const MAX_MINES = 24;
const DEFAULT_MINES = 3;

type Phase = 'idle' | 'active' | 'lost' | 'cashed_out';

const BG = '#0f1320';
const PANEL = '#171c28';
const FIELD = '#0c1018';
const BORDER = '#222a38';
const GREEN = '#1bd96a';
const RED = '#ff6b6b';
const TEXT = '#e6e9ef';
const MUTED = '#8b93a7';

// A projection is the untyped `{[k]: unknown}` the server returns — read each key
// individually with a narrow cast (nothing is type-checked across the seam).
type Projection = { [key: string]: unknown };

function num(value: unknown): number | null {
  return typeof value === 'number' ? value : null;
}

function fmtMult(value: number | null): string {
  return value === null ? '—' : `${value.toFixed(2)}×`;
}

export default function MinesView() {
  const client = useGameClient();
  const { currency, refreshBalance } = useSession();

  const [mines, setMines] = useState(DEFAULT_MINES);
  const [phase, setPhase] = useState<Phase>('idle');
  const [roundId, setRoundId] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  const [k, setK] = useState(0);
  const [currentMultiplier, setCurrentMultiplier] = useState<number | null>(
    null,
  );
  const [nextMultiplier, setNextMultiplier] = useState<number | null>(null);
  const [minePositions, setMinePositions] = useState<number[]>([]);
  const [hitCell, setHitCell] = useState<number | null>(null);
  const [payoutMinor, setPayoutMinor] = useState<number | null>(null);
  const [cashoutMultiplier, setCashoutMultiplier] = useState<number | null>(
    null,
  );

  const [rejection, setRejection] = useState<string | null>(null);
  const [transportError, setTransportError] = useState<string | null>(null);

  const terminal = phase === 'lost' || phase === 'cashed_out';

  function resetRound(): void {
    setPhase('idle');
    setRoundId(null);
    setRevealed(new Set());
    setK(0);
    setCurrentMultiplier(null);
    setNextMultiplier(null);
    setMinePositions([]);
    setHitCell(null);
    setPayoutMinor(null);
    setCashoutMultiplier(null);
    setRejection(null);
    setTransportError(null);
  }

  function setMinesClamped(value: number): void {
    if (Number.isNaN(value)) return;
    setMines(Math.min(MAX_MINES, Math.max(MIN_MINES, Math.trunc(value))));
  }

  async function openRound(stake: number): Promise<void> {
    setRejection(null);
    setTransportError(null);
    try {
      // betId is a client IDEMPOTENCY key, not entropy. The server commits the
      // mine layout from its own seeds and sets GameRound.id = betId.
      const bet = await client.bet(GAME_ID, {
        betId: crypto.randomUUID(),
        stakeMinor: stake,
        currency,
        mode: 'PLAY',
        input: { mines },
      });
      const open = bet.outcome as Projection;
      setRoundId(bet.betId);
      setRevealed(new Set());
      setK(num(open.k) ?? 0);
      setCurrentMultiplier(num(open.currentMultiplier)); // absent at k=0 → null
      setNextMultiplier(num(open.nextMultiplier));
      setMinePositions([]);
      setHitCell(null);
      setPayoutMinor(null);
      setCashoutMultiplier(null);
      setPhase('active');
      // Stake was debited at open — re-fetch the server-authoritative balance.
      await refreshBalance();
    } catch (err) {
      if (err instanceof BetRejectedError) {
        setRejection(err.reason);
        return;
      }
      setTransportError('Something went wrong. Please try again.');
    }
  }

  async function reveal(cell: number): Promise<void> {
    if (phase !== 'active' || roundId === null || revealed.has(cell)) return;
    setTransportError(null);
    try {
      const proj = (await client.action(GAME_ID, {
        roundId,
        op: 'reveal',
        cell,
      })) as Projection;
      const status = proj.status;
      if (status === 'LOST') {
        setHitCell(cell);
        setMinePositions((proj.minePositions as number[]) ?? []);
        setPhase('lost');
        await refreshBalance(); // a loss credits nothing; balance re-read regardless
        return;
      }
      // Safe reveal: render the server multipliers verbatim, advance k, paint cell.
      setRevealed((prev) => new Set(prev).add(cell));
      setK(num(proj.k) ?? k);
      setCurrentMultiplier(num(proj.currentMultiplier));
      setNextMultiplier(num(proj.nextMultiplier));
    } catch {
      setTransportError('Something went wrong. Please try again.');
    }
  }

  async function cashout(): Promise<void> {
    if (phase !== 'active' || roundId === null || !canCashout(k)) return;
    setTransportError(null);
    try {
      const proj = (await client.action(GAME_ID, {
        roundId,
        op: 'cashout',
      })) as Projection;
      setMinePositions((proj.minePositions as number[]) ?? []);
      setCashoutMultiplier(num(proj.multiplier));
      setPayoutMinor(num(proj.payoutMinor));
      setPhase('cashed_out');
      await refreshBalance(); // a win credits at cashout — re-read the balance
    } catch {
      setTransportError('Something went wrong. Please try again.');
    }
  }

  return (
    <section
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(260px, 300px) 1fr',
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
      {/* ---- Left bet panel ------------------------------------------------- */}
      <div style={{ background: PANEL, padding: 16, display: 'grid', gap: 14 }}>
        <div style={{ display: 'grid', gap: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
            Mines
          </span>
          <input
            type="number"
            aria-label="Mines"
            min={MIN_MINES}
            max={MAX_MINES}
            step={1}
            value={mines}
            disabled={phase === 'active'}
            onChange={(e) => setMinesClamped(Number(e.target.value))}
            style={{ ...fieldStyle, width: '100%' }}
          />
        </div>

        {phase !== 'active' && (
          <BetControls
            onBet={(stake) => void openRound(stake)}
            currency={currency}
            rejectionReason={rejection}
          />
        )}

        {phase === 'active' && (
          <button
            type="button"
            onClick={() => void cashout()}
            disabled={!canCashout(k)}
            style={cashoutStyle(canCashout(k))}
          >
            Cash Out {currentMultiplier !== null && fmtMult(currentMultiplier)}
          </button>
        )}

        {terminal && (
          <button type="button" onClick={resetRound} style={newGameStyle}>
            New game
          </button>
        )}

        {transportError !== null && (
          <p
            role="alert"
            data-testid="mines-error"
            style={{ margin: 0, color: RED, fontSize: 13 }}
          >
            {transportError}
          </p>
        )}
      </div>

      {/* ---- Right stage --------------------------------------------------- */}
      <div
        style={{
          background: BG,
          padding: 24,
          display: 'grid',
          gap: 20,
          alignContent: 'start',
          justifyItems: 'center',
        }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: 12,
            width: '100%',
            maxWidth: 480,
          }}
        >
          <Stat label="Current">
            <span data-testid="mines-current" style={fieldStyle}>
              {fmtMult(phase === 'idle' ? null : currentMultiplier)}
            </span>
          </Stat>
          <Stat label="Next">
            <span style={fieldStyle}>
              {fmtMult(phase === 'active' ? nextMultiplier : null)}
            </span>
          </Stat>
        </div>

        {phase === 'idle' ? (
          <p style={{ color: MUTED, margin: 0 }}>
            Pick your mines and place a bet to start a round.
          </p>
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: `repeat(${GRID_COLS}, 1fr)`,
              gap: 8,
              width: '100%',
              maxWidth: 480,
            }}
          >
            {gridCells().map((cell) => {
              const isSafe = revealed.has(cell);
              const mine = terminal && isMine(cell, minePositions);
              return (
                <button
                  key={cell}
                  type="button"
                  aria-label={`Cell ${cell}`}
                  data-mine={mine ? 'true' : 'false'}
                  data-safe={isSafe ? 'true' : 'false'}
                  disabled={terminal || isSafe}
                  onClick={() => void reveal(cell)}
                  style={cellStyle({
                    safe: isSafe,
                    mine,
                    hit: terminal && cell === hitCell,
                  })}
                >
                  {mine ? '💣' : isSafe ? '💎' : ''}
                </button>
              );
            })}
          </div>
        )}

        {phase === 'lost' && (
          <p
            data-testid="mines-result"
            style={{ margin: 0, color: RED, fontWeight: 800, fontSize: 18 }}
          >
            Busted — hit a mine.
          </p>
        )}

        {phase === 'cashed_out' && (
          <div
            data-testid="mines-result"
            style={{
              display: 'grid',
              gap: 4,
              justifyItems: 'center',
              color: GREEN,
            }}
          >
            <span style={{ fontWeight: 800, fontSize: 18 }}>
              Cashed out {fmtMult(cashoutMultiplier)}
            </span>
            <span data-testid="mines-payout" style={{ fontWeight: 700 }}>
              {formatMinor(payoutMinor ?? 0, currency)} {currency}
            </span>
          </div>
        )}
      </div>
    </section>
  );
}

function Stat(props: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: 4 }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
        {props.label}
      </span>
      {props.children}
    </div>
  );
}

const fieldStyle: React.CSSProperties = {
  font: 'inherit',
  padding: '8px 10px',
  borderRadius: 8,
  border: `1px solid ${BORDER}`,
  background: FIELD,
  color: TEXT,
  fontVariantNumeric: 'tabular-nums',
  textAlign: 'center',
};

function cellStyle(opts: {
  safe: boolean;
  mine: boolean;
  hit: boolean;
}): React.CSSProperties {
  const background = opts.mine
    ? opts.hit
      ? '#5a1620'
      : '#2a161b'
    : opts.safe
      ? '#10301d'
      : '#1b2230';
  return {
    font: 'inherit',
    fontSize: 22,
    aspectRatio: '1 / 1',
    borderRadius: 10,
    border: `1px solid ${opts.mine ? RED : BORDER}`,
    background,
    color: TEXT,
    cursor: opts.safe ? 'default' : 'pointer',
    display: 'grid',
    placeItems: 'center',
  };
}

function cashoutStyle(enabled: boolean): React.CSSProperties {
  return {
    font: 'inherit',
    fontWeight: 800,
    fontSize: 15,
    padding: '12px 16px',
    borderRadius: 8,
    border: 'none',
    background: enabled ? GREEN : '#2a3140',
    color: enabled ? '#06210f' : MUTED,
    cursor: enabled ? 'pointer' : 'not-allowed',
  };
}

const newGameStyle: React.CSSProperties = {
  font: 'inherit',
  fontWeight: 700,
  fontSize: 15,
  padding: '12px 16px',
  borderRadius: 8,
  border: `1px solid ${BORDER}`,
  background: '#1b2230',
  color: TEXT,
  cursor: 'pointer',
};
