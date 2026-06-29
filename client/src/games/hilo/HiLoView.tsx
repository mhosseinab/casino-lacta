import { useState } from 'react';
import { BetControls } from '../../components/BetControls';
import { formatMinor } from '../../lib/money';
import { useSession } from '../../lib/session';
import { BetRejectedError, useGameClient } from '../../lib/transport';
import { canCashout, pHigher, pLower, rankLabel } from './hiLoMath';

// HiLoView — a STATEFUL Original (spec §A.7). THIN renderer: it opens a round,
// sends per-step guesses / cashout through the GameClient seam, and renders the
// server's projections VERBATIM. It decides NO outcome, multiplier, payout, or
// balance — the shown card, currentMultiplier, win/loss and payoutMinor all come
// off the server (bet.outcome at open; the /action projection per step). The
// per-side win probabilities shown are a pure PREVIEW (hiLoMath.ts); the
// authoritative result is always the server projection.
//
// Money flow (bet_loop.py): DEBIT at open, CREDIT only at cashout. So the balance
// is re-fetched after open and after any TERMINAL action (LOST/CASHED_OUT) — never
// on a winning guess, which moves no money.
const GAME_ID = 'originals.hilo';

type Status = 'ACTIVE' | 'LOST' | 'CASHED_OUT';

interface Round {
  roundId: string;
  status: Status;
  /** The current shown card (or the losing revealed card once LOST). */
  card: number;
  currentMultiplier: number;
  steps: number;
  payoutMinor: number | null;
}

const BG = '#0f1320';
const PANEL = '#171c28';
const FIELD = '#0c1018';
const BORDER = '#222a38';
const GREEN = '#1bd96a';
const RED = '#ff6b6b';
const TEXT = '#e6e9ef';
const MUTED = '#8b93a7';

export default function HiLoView() {
  const client = useGameClient();
  const { balanceMinor, currency, refreshBalance } = useSession();

  const [round, setRound] = useState<Round | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  const [transportError, setTransportError] = useState<string | null>(null);
  const [inFlight, setInFlight] = useState(false);

  async function openRound(stake: number): Promise<void> {
    setRejection(null);
    setTransportError(null);
    setInFlight(true);
    try {
      // betId is a client-generated IDEMPOTENCY key (the round id), NOT entropy.
      // HiLo has no game-specific open input — the only client input is the
      // per-step `side`, validated server-side (hilo.py).
      const bet = await client.bet(GAME_ID, {
        betId: crypto.randomUUID(),
        stakeMinor: stake,
        currency,
        mode: 'PLAY',
        input: {},
      });
      // The open BetObject.outcome IS HiLo's public_view snapshot (ACTIVE).
      const open = bet.outcome;
      setRound({
        roundId: bet.betId,
        status: 'ACTIVE',
        card: open.shownRank as number,
        currentMultiplier: open.currentMultiplier as number,
        steps: open.steps as number,
        payoutMinor: null,
      });
      await refreshBalance(); // DEBIT at open
    } catch (err) {
      if (err instanceof BetRejectedError) {
        setRound(null);
        setRejection(err.reason);
        return;
      }
      setTransportError('Something went wrong. Please try again.');
    } finally {
      setInFlight(false);
    }
  }

  async function guess(side: 'HIGHER' | 'LOWER'): Promise<void> {
    if (round === null || round.status !== 'ACTIVE') return;
    setInFlight(true);
    setTransportError(null);
    try {
      const result = await client.action(GAME_ID, {
        roundId: round.roundId,
        op: 'guess',
        side,
      });
      const status = result.status as string;
      if (status === 'ACTIVE') {
        // A correct call: the revealed card becomes the new shown card; the
        // multiplier compounds. No money moved → no balance refresh.
        setRound({
          ...round,
          status: 'ACTIVE',
          card: result.shownRank as number,
          currentMultiplier: result.currentMultiplier as number,
          steps: result.steps as number,
        });
      } else if (status === 'LOST') {
        // Busted: reveal the losing card (the LOST projection has no shownRank).
        setRound({
          ...round,
          status: 'LOST',
          card: result.revealedRank as number,
          steps: result.steps as number,
        });
        await refreshBalance(); // terminal — a loss credits nothing
      }
    } catch {
      // A transport fault mid-round: the round is unchanged server-side; surface a
      // neutral message (never the raw error) and let the player retry the guess.
      setTransportError('Something went wrong. Please try again.');
    } finally {
      setInFlight(false);
    }
  }

  async function cashOut(): Promise<void> {
    if (round === null || round.status !== 'ACTIVE' || !canCashout(round.steps))
      return;
    setInFlight(true);
    setTransportError(null);
    try {
      const result = await client.action(GAME_ID, {
        roundId: round.roundId,
        op: 'cashout',
      });
      setRound({
        ...round,
        status: 'CASHED_OUT',
        currentMultiplier: result.currentMultiplier as number,
        steps: result.steps as number,
        payoutMinor: result.payoutMinor as number,
      });
      await refreshBalance(); // CREDIT at cashout
    } catch {
      // A failed cashout must not fail silently in a money game: surface a neutral
      // message. The round stays ACTIVE server-side, so the player can retry.
      setTransportError('Something went wrong. Please try again.');
    } finally {
      setInFlight(false);
    }
  }

  function reset(): void {
    setRound(null);
    setRejection(null);
    setTransportError(null);
  }

  const active = round !== null && round.status === 'ACTIVE';
  const guessDisabled = !active || inFlight;
  const cashoutDisabled = !active || inFlight || !canCashout(round?.steps ?? 0);

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
        {round === null ? (
          <BetControls
            onBet={(stake) => void openRound(stake)}
            currency={currency}
            balanceMinor={balanceMinor}
            rejectionReason={rejection}
          />
        ) : (
          <div style={{ display: 'grid', gap: 10 }}>
            <span style={{ fontSize: 12, color: MUTED }}>
              {round.status === 'ACTIVE'
                ? 'Round open — guess to compound, or cash out.'
                : 'Round over.'}
            </span>
            <button type="button" onClick={reset} style={resetButtonStyle}>
              New game
            </button>
          </div>
        )}

        {transportError !== null && (
          <p
            role="alert"
            data-testid="hilo-error"
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
        }}
      >
        {round === null ? (
          <p style={{ color: MUTED, margin: 0 }}>
            Place a bet to deal the first card.
          </p>
        ) : (
          <>
            {/* The current card — server's shownRank (or the losing card on LOST). */}
            <div
              style={{
                display: 'grid',
                gap: 6,
                justifyItems: 'center',
                background: PANEL,
                border: `1px solid ${BORDER}`,
                borderRadius: 12,
                padding: 24,
              }}
            >
              <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
                Current card
              </span>
              <span
                data-testid="hilo-card"
                style={{
                  fontSize: 56,
                  fontWeight: 800,
                  color: round.status === 'LOST' ? RED : TEXT,
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {rankLabel(round.card)}
              </span>
            </div>

            {/* Higher / Lower — annotated with each side's PREVIEW win chance. */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 12,
              }}
            >
              <button
                type="button"
                onClick={() => void guess('HIGHER')}
                disabled={guessDisabled}
                style={sideButtonStyle(guessDisabled)}
              >
                ▲ Higher
                <small style={{ display: 'block', fontWeight: 500 }}>
                  {(pHigher(round.card) * 100).toFixed(1)}%
                </small>
              </button>
              <button
                type="button"
                onClick={() => void guess('LOWER')}
                disabled={guessDisabled}
                style={sideButtonStyle(guessDisabled)}
              >
                ▼ Lower
                <small style={{ display: 'block', fontWeight: 500 }}>
                  {(pLower(round.card) * 100).toFixed(1)}%
                </small>
              </button>
            </div>

            {/* Running stats — all server-sent. */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(2, 1fr)',
                gap: 12,
                background: PANEL,
                border: `1px solid ${BORDER}`,
                borderRadius: 10,
                padding: 16,
              }}
            >
              <Stat label="Multiplier">
                <span data-testid="hilo-multiplier" style={fieldStyle}>
                  {round.currentMultiplier.toFixed(2)}×
                </span>
              </Stat>
              <Stat label="Steps">
                <span data-testid="hilo-steps" style={fieldStyle}>
                  {round.steps}
                </span>
              </Stat>
            </div>

            <button
              type="button"
              onClick={() => void cashOut()}
              disabled={cashoutDisabled}
              style={cashoutButtonStyle(cashoutDisabled)}
            >
              Cash out
            </button>

            {round.status === 'LOST' && (
              <output
                data-testid="hilo-busted"
                style={{
                  display: 'block',
                  margin: 0,
                  color: RED,
                  fontWeight: 700,
                }}
              >
                Busted — no payout.
              </output>
            )}
            {round.status === 'CASHED_OUT' && round.payoutMinor !== null && (
              <output
                data-testid="hilo-cashed-out"
                style={{
                  display: 'block',
                  margin: 0,
                  color: GREEN,
                  fontWeight: 700,
                }}
              >
                Cashed out {formatMinor(round.payoutMinor, currency)} {currency}
              </output>
            )}
          </>
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
};

function sideButtonStyle(disabled: boolean): React.CSSProperties {
  return {
    font: 'inherit',
    fontWeight: 800,
    fontSize: 16,
    padding: '16px 12px',
    borderRadius: 10,
    border: `1px solid ${BORDER}`,
    background: disabled ? '#11151f' : '#1b2230',
    color: disabled ? MUTED : TEXT,
    cursor: disabled ? 'not-allowed' : 'pointer',
  };
}

function cashoutButtonStyle(disabled: boolean): React.CSSProperties {
  return {
    font: 'inherit',
    fontWeight: 800,
    fontSize: 15,
    padding: '12px 16px',
    borderRadius: 8,
    border: 'none',
    background: disabled ? '#1b3a26' : GREEN,
    color: disabled ? MUTED : '#06210f',
    cursor: disabled ? 'not-allowed' : 'pointer',
  };
}

const resetButtonStyle: React.CSSProperties = {
  font: 'inherit',
  fontWeight: 700,
  padding: '10px 14px',
  borderRadius: 8,
  border: `1px solid ${BORDER}`,
  background: '#1b2230',
  color: TEXT,
  cursor: 'pointer',
};
