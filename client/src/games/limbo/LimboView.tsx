import { useState } from 'react';
import { BetControls } from '../../components/BetControls';
import { ResultPanel } from '../../components/ResultPanel';
import type { BetObject } from '../../contracts';
import { formatMinor } from '../../lib/money';
import { useSession } from '../../lib/session';
import { BetRejectedError, useGameClient } from '../../lib/transport';
import { LimboMeter } from './LimboMeter';
import { LIMBO_TARGET_MIN, profitMinor, winChancePct } from './limboMath';

// LimboView — an instant Original built on the S9 Dice reference pattern. THIN
// renderer: it sends intent (stake + target multiplier) through the GameClient
// seam and renders the server's BetObject VERBATIM. It computes no outcome,
// payout, or balance — the generated multiplier / win / payout all come off
// `bet.outcome`, and the post-settle balance is RE-FETCHED from /me.
//
// The win-chance / potential-profit shown are a pre-bet QUOTE (limboMath.ts); the
// authoritative multiplier & payout are always the server's `bet.outcome` values
// (rendered by ResultPanel). The quote's only economy input is LIMBO_PREVIEW_EDGE.
const GAME_ID = 'originals.limbo';

const BG = '#0f1320';
const PANEL = '#171c28';
const FIELD = '#0c1018';
const BORDER = '#222a38';
const GREEN = '#1bd96a';
const TEXT = '#e6e9ef';
const MUTED = '#8b93a7';

export default function LimboView() {
  const client = useGameClient();
  const { balanceMinor, currency, refreshBalance } = useSession();

  const [target, setTarget] = useState(2.0);
  const [stakeMinor, setStakeMinor] = useState<number | null>(100);
  const [bet, setBet] = useState<BetObject | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  // Neutral transport-fault notice, distinct from a server `rejectionReason`.
  const [transportError, setTransportError] = useState<string | null>(null);

  // Pre-bet quote (preview). Authoritative values arrive on the server result.
  const winPct = winChancePct(target);
  const profit = stakeMinor != null ? profitMinor(stakeMinor, target) : 0;

  function setTargetClamped(value: number): void {
    if (Number.isNaN(value)) return;
    // Lower-bounded by the engine floor; the server validates the upper cap
    // (maxMultiplier per currency, §2.5) — never decided here.
    setTarget(Math.max(LIMBO_TARGET_MIN, value));
  }

  async function placeBet(stake: number): Promise<void> {
    setRejection(null);
    setTransportError(null);
    try {
      // betId is a client-generated IDEMPOTENCY key, NOT entropy — the server
      // derives the generated multiplier from its own seeds.
      const result = await client.bet(GAME_ID, {
        betId: crypto.randomUUID(),
        stakeMinor: stake,
        currency,
        mode: 'PLAY',
        input: { target },
      });
      setBet(result);
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
            Target Multiplier
          </span>
          <input
            type="number"
            aria-label="Target multiplier"
            min={LIMBO_TARGET_MIN}
            step={0.01}
            value={target}
            onChange={(e) => setTargetClamped(Number(e.target.value))}
            style={{ ...fieldStyle, width: '100%' }}
          />
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
            data-testid="limbo-error"
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
        <LimboMeter
          target={target}
          generated={bet ? (bet.outcome.generated as number) : null}
          betId={bet?.betId}
          won={bet ? (bet.outcome.won as boolean) : undefined}
        />

        {/* Quote stats — Target Multiplier | Win Chance (both derived/preview). */}
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
          <Stat label="Target Multiplier">
            <span style={fieldStyle}>{target.toFixed(2)}×</span>
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
