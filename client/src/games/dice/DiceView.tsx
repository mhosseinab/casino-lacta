import { useState } from 'react';
import { BetControls } from '../../components/BetControls';
import { ResultPanel } from '../../components/ResultPanel';
import type { BetObject } from '../../contracts';
import { formatMinor } from '../../lib/money';
import { useSession } from '../../lib/session';
import { BetRejectedError, useGameClient } from '../../lib/transport';
import { DiceSlider } from './DiceSlider';
import {
  type DiceDirection,
  multiplierFor,
  profitMinor,
  winChancePct,
} from './diceMath';

// DiceView — the canonical Original view and the REFERENCE PATTERN for S11–S24.
// It is a THIN renderer: it sends intent (stake + target/direction) through the
// GameClient seam, then renders the server's BetObject VERBATIM. It computes no
// outcome, payout, or balance — the roll/multiplier/payout all come off the server
// result, and the post-settle balance is RE-FETCHED from /me (never derived here).
//
// The multiplier / win-chance / potential-profit shown beside the slider are a
// pre-bet QUOTE (diceMath.ts) — a live preview, exactly like Gamdom/Stake. The
// authoritative multiplier & payout are always the server's `bet.outcome` values
// (rendered by ResultPanel); the quote's only economy input is DICE_PREVIEW_EDGE,
// the single named owner of the edge in the client (see diceMath.ts header).
const GAME_ID = 'originals.dice';

// Dark casino palette (self-contained so the view reads as a casino table regardless
// of the surrounding shell). Cosmetic only.
const BG = '#0f1320';
const PANEL = '#171c28';
const FIELD = '#0c1018';
const BORDER = '#222a38';
const GREEN = '#1bd96a';
const TEXT = '#e6e9ef';
const MUTED = '#8b93a7';

export default function DiceView() {
  const client = useGameClient();
  const { balanceMinor, currency, refreshBalance } = useSession();

  const [target, setTarget] = useState(50);
  const [direction, setDirection] = useState<DiceDirection>('OVER');
  const [stakeMinor, setStakeMinor] = useState<number | null>(100);
  const [bet, setBet] = useState<BetObject | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  // A NEUTRAL transport-fault notice, distinct from a server `rejectionReason`.
  // Set for any non-BetRejectedError (HTTP 5xx, network drop, JSON parse) so a
  // fault doesn't become a silent unhandled rejection. Reference pattern S11–S24.
  const [transportError, setTransportError] = useState<string | null>(null);

  // Pre-bet quote (preview). Authoritative values arrive on the server result.
  const winPct = winChancePct(target, direction);
  const multiplier = multiplierFor(winPct);
  const profit = stakeMinor != null ? profitMinor(stakeMinor, multiplier) : 0;

  function toggleDirection(): void {
    setDirection((d) => (d === 'OVER' ? 'UNDER' : 'OVER'));
  }

  function setTargetClamped(value: number): void {
    if (Number.isNaN(value)) return;
    setTarget(Math.min(99.99, Math.max(0.01, value)));
  }

  async function placeBet(stake: number): Promise<void> {
    setRejection(null);
    setTransportError(null);
    try {
      // betId is a client-generated IDEMPOTENCY key, NOT outcome entropy — the
      // server derives the roll from its own seeds. The client never generates
      // game randomness (no client-side RNG); crypto.randomUUID is a key only.
      const result = await client.bet(GAME_ID, {
        betId: crypto.randomUUID(),
        stakeMinor: stake,
        currency,
        mode: 'PLAY',
        input: { target, direction },
      });
      setBet(result);
      // Server-authoritative: re-read the balance, never adjust it locally.
      await refreshBalance();
    } catch (err) {
      // A server rule refusal (min/max-bet, RG limit) — surface the reason verbatim.
      if (err instanceof BetRejectedError) {
        setBet(null);
        setRejection(err.reason);
        return;
      }
      // Any other failure (transport/server/parse): show a neutral message and
      // never leak the raw error. If bet() itself failed no result was set.
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
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 4,
            background: FIELD,
            borderRadius: 8,
            padding: 4,
          }}
        >
          <button type="button" style={tabStyle(true)}>
            Manual
          </button>
          <button
            type="button"
            disabled
            title="Auto betting — coming soon"
            style={tabStyle(false)}
          >
            Auto
          </button>
        </div>

        <BetControls
          onBet={(stake) => void placeBet(stake)}
          onStakeChange={setStakeMinor}
          currency={currency}
          balanceMinor={balanceMinor}
          rejectionReason={rejection}
        />

        <div style={{ display: 'grid', gap: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
            Potential Profit
          </span>
          <output
            aria-label="Potential profit"
            style={{
              ...fieldStyle,
              display: 'block',
              color: profit > 0 ? GREEN : TEXT,
            }}
          >
            {formatMinor(profit, currency)} {currency}
          </output>
        </div>

        {transportError !== null && (
          <p
            role="alert"
            data-testid="dice-error"
            style={{ margin: 0, color: '#ff6b6b', fontSize: 13 }}
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
        <DiceSlider
          target={target}
          direction={direction}
          onTargetChange={setTargetClamped}
          roll={bet ? (bet.outcome.roll as number) : null}
          betId={bet?.betId}
          won={bet ? (bet.outcome.won as boolean) : undefined}
        />

        {/* Quote stats — Multiplier (derived) | Roll Over/Under (input) | Win Chance (derived). */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 12,
            background: PANEL,
            border: `1px solid ${BORDER}`,
            borderRadius: 10,
            padding: 16,
          }}
        >
          <Stat label="Multiplier">
            <span style={fieldStyle}>{multiplier.toFixed(2)}×</span>
          </Stat>

          <Stat label={direction === 'OVER' ? 'Roll Over' : 'Roll Under'}>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                type="number"
                aria-label="Roll target"
                min={0.01}
                max={99.99}
                step={0.01}
                value={target}
                onChange={(e) => setTargetClamped(Number(e.target.value))}
                style={{ ...fieldStyle, flex: 1, width: '100%' }}
              />
              <button
                type="button"
                aria-label="Switch over/under"
                onClick={toggleDirection}
                style={{
                  ...fieldStyle,
                  cursor: 'pointer',
                  width: 40,
                  textAlign: 'center',
                }}
              >
                ⇄
              </button>
            </div>
          </Stat>

          <Stat label="Win Chance">
            <span style={fieldStyle}>{winPct.toFixed(2)} %</span>
          </Stat>
        </div>

        {bet && <ResultPanel bet={bet} />}
      </div>
    </section>
  );
}

// A labelled stat cell. A plain div (not <label>) because two of the three cells
// display derived text, not a form control; the editable Roll-target input carries
// its own aria-label.
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
  border: '1px solid #222a38',
  background: '#0c1018',
  color: '#e6e9ef',
  fontVariantNumeric: 'tabular-nums',
};

function tabStyle(active: boolean): React.CSSProperties {
  return {
    font: 'inherit',
    fontWeight: 700,
    padding: '8px 12px',
    borderRadius: 6,
    border: 'none',
    cursor: active ? 'default' : 'not-allowed',
    background: active ? '#171c28' : 'transparent',
    color: active ? '#1bd96a' : '#8b93a7',
  };
}
