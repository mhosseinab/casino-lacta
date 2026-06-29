import { useState } from 'react';
import { BetControls } from '../../components/BetControls';
import { ResultPanel } from '../../components/ResultPanel';
import type { BetObject } from '../../contracts';
import { formatMinor } from '../../lib/money';
import { useSession } from '../../lib/session';
import { BetRejectedError, useGameClient } from '../../lib/transport';
import { PocketDiceFaces } from './PocketDiceFaces';
import {
  type PocketDiceDirection,
  multiplierFor,
  profitMinor,
  winChancePct,
} from './pocketDiceMath';

// PocketDiceView — an instant Original built on the S9 Dice / S11 Limbo reference
// pattern. THIN renderer: it sends intent (stake + target sum + direction) through the
// GameClient seam and renders the server's BetObject VERBATIM. It computes no outcome,
// payout, or balance — the dice/sum/multiplier/payout all come off `bet.outcome`, and
// the post-settle balance is RE-FETCHED from /me.
//
// The win-chance / multiplier / profit shown are a pre-bet QUOTE (pocketDiceMath.ts);
// the authoritative multiplier & payout are always the server's `bet.outcome` values
// (rendered by ResultPanel). The quote's only economy input is POCKETDICE_PREVIEW_EDGE.
const GAME_ID = 'originals.pocketdice';

// 2d6 sum is in [2,12]. The forbidden zero-win bets (UNDER 2, OVER 12) are made
// UNSELECTABLE here by clamping the target into the per-direction valid window.
const SUM_MIN = 2;
const SUM_MAX = 12;

/** Clamp the target sum into the valid (non-forbidden) window for a direction:
 *  UNDER needs a target > 2 (UNDER 2 can never win); OVER needs a target < 12. */
function clampTarget(value: number, direction: PocketDiceDirection): number {
  const lo = direction === 'UNDER' ? SUM_MIN + 1 : SUM_MIN;
  const hi = direction === 'OVER' ? SUM_MAX - 1 : SUM_MAX;
  return Math.min(hi, Math.max(lo, Math.round(value)));
}

const BG = '#0f1320';
const PANEL = '#171c28';
const FIELD = '#0c1018';
const BORDER = '#222a38';
const GREEN = '#1bd96a';
const TEXT = '#e6e9ef';
const MUTED = '#8b93a7';

export default function PocketDiceView() {
  const client = useGameClient();
  const { balanceMinor, currency, refreshBalance } = useSession();

  const [target, setTarget] = useState(7);
  const [direction, setDirection] = useState<PocketDiceDirection>('UNDER');
  const [stakeMinor, setStakeMinor] = useState<number | null>(100);
  const [bet, setBet] = useState<BetObject | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  // Neutral transport-fault notice, distinct from a server `rejectionReason`.
  const [transportError, setTransportError] = useState<string | null>(null);

  // Pre-bet quote (preview). Authoritative values arrive on the server result.
  const winPct = winChancePct(target, direction);
  const multiplier = multiplierFor(target, direction);
  const profit =
    stakeMinor != null ? profitMinor(stakeMinor, target, direction) : 0;

  function toggleDirection(): void {
    setDirection((d) => {
      const next: PocketDiceDirection = d === 'OVER' ? 'UNDER' : 'OVER';
      // Re-clamp the current target so a switch never lands on a forbidden bet
      // (e.g. UNDER 12 → OVER must snap to 11; OVER 2 → UNDER must snap to 3).
      setTarget((t) => clampTarget(t, next));
      return next;
    });
  }

  function setTargetClamped(value: number): void {
    if (Number.isNaN(value)) return;
    setTarget(clampTarget(value, direction));
  }

  async function placeBet(stake: number): Promise<void> {
    setRejection(null);
    setTransportError(null);
    try {
      // betId is a client-generated IDEMPOTENCY key, NOT entropy — the server rolls
      // the dice from its own seeds. The client never generates game randomness.
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
      if (err instanceof BetRejectedError) {
        setBet(null);
        setRejection(err.reason);
        return;
      }
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

        <div style={{ display: 'grid', gap: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
            {direction === 'UNDER' ? 'Roll Under' : 'Roll Over'} (sum)
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              type="number"
              aria-label="Target sum"
              min={SUM_MIN}
              max={SUM_MAX}
              step={1}
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
            data-testid="pocketdice-error"
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
        <PocketDiceFaces
          target={target}
          direction={direction}
          dice={bet ? (bet.outcome.dice as [number, number]) : null}
          sum={bet ? (bet.outcome.sum as number) : null}
          betId={bet?.betId}
          won={bet ? (bet.outcome.won as boolean) : undefined}
        />

        {/* Quote stats — Multiplier | Win Chance (both derived/preview). */}
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
            <span style={fieldStyle}>{multiplier.toFixed(2)}×</span>
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

// A labelled stat cell (display text, not a form control → a plain div).
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
