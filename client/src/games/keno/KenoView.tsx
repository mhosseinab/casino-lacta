import { useState } from 'react';
import { BetControls } from '../../components/BetControls';
import { ResultPanel } from '../../components/ResultPanel';
import type { BetObject } from '../../contracts';
import { useSession } from '../../lib/session';
import { BetRejectedError, useGameClient } from '../../lib/transport';
import { KenoGrid } from './KenoGrid';
import { isValidSelection, togglePick } from './kenoMath';

// KenoView — an instant Original on the S9 Dice / S11 Limbo reference pattern. THIN
// renderer: it sends intent (the chosen picks + risk) through the GameClient seam and
// renders the server's BetObject VERBATIM. It computes no outcome, payout, multiplier
// or balance — the drawn set, hit count, multiplier and payout all come off
// `bet.outcome`, and the post-settle balance is RE-FETCHED from /me.
//
// Unlike Dice/Limbo there is deliberately NO pre-bet quote: Keno's payout is a
// server-owned per-(picks,risk) table, so quoting a multiplier/win-chance here would
// mean replicating it client-side. We keep it honest and show nothing economic until
// the server result arrives.
const GAME_ID = 'originals.keno';

const RISKS = ['LOW', 'MEDIUM', 'HIGH'] as const;
type Risk = (typeof RISKS)[number];

const BG = '#0f1320';
const PANEL = '#171c28';
const FIELD = '#0c1018';
const BORDER = '#222a38';
const TEXT = '#e6e9ef';
const MUTED = '#8b93a7';
const GREEN = '#1bd96a';

export default function KenoView() {
  const client = useGameClient();
  const { balanceMinor, currency, refreshBalance } = useSession();

  const [picks, setPicks] = useState<number[]>([]);
  const [risk, setRisk] = useState<Risk>('MEDIUM');
  const [bet, setBet] = useState<BetObject | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  // A NEUTRAL transport-fault notice, distinct from a server `rejectionReason`.
  const [transportError, setTransportError] = useState<string | null>(null);

  function toggle(n: number): void {
    // Editing the selection clears the previous result so the stale drawn reveal
    // never lingers — the displayed picks always match the displayed draw.
    setBet(null);
    setSelectionError(null);
    setPicks((current) => togglePick(current, n));
  }

  async function placeBet(stake: number): Promise<void> {
    setRejection(null);
    setSelectionError(null);
    setTransportError(null);
    if (!isValidSelection(picks)) {
      setSelectionError('Select at least 1 number to bet.');
      return;
    }
    try {
      // betId is a client-generated IDEMPOTENCY key, NOT entropy — the server draws
      // from its own seeds. The client never generates game randomness.
      const result = await client.bet(GAME_ID, {
        betId: crypto.randomUUID(),
        stakeMinor: stake,
        currency,
        mode: 'PLAY',
        input: { picks, risk },
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

  // On a result the grid reveals the SERVER draw against the SERVER-recorded picks;
  // otherwise it shows the live selection. The hit COUNT is the server value verbatim.
  const drawn = bet ? (bet.outcome.drawn as number[]) : null;
  const hits = bet ? (bet.outcome.hits as number) : null;
  const resultPicks = bet ? (bet.outcome.picks as number[]) : picks;

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
          currency={currency}
          balanceMinor={balanceMinor}
          rejectionReason={rejection}
        />

        <div style={{ display: 'grid', gap: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
            Risk
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            {RISKS.map((r) => (
              <button
                key={r}
                type="button"
                aria-pressed={risk === r}
                onClick={() => setRisk(r)}
                style={riskStyle(risk === r)}
              >
                {r.charAt(0) + r.slice(1).toLowerCase()}
              </button>
            ))}
          </div>
        </div>

        <p style={{ margin: 0, fontSize: 12, color: MUTED }}>
          Select up to 10 numbers ({picks.length}/10 chosen).
        </p>

        {selectionError !== null && (
          <p role="alert" style={{ margin: 0, color: '#ff6b6b', fontSize: 13 }}>
            {selectionError}
          </p>
        )}

        {transportError !== null && (
          <p
            role="alert"
            data-testid="keno-error"
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
        <KenoGrid
          key={bet?.betId ?? 'idle'}
          picks={resultPicks}
          drawn={drawn}
          betId={bet?.betId}
          onToggle={toggle}
        />

        {bet && (
          <div
            style={{
              display: 'grid',
              gap: 12,
              background: PANEL,
              border: `1px solid ${BORDER}`,
              borderRadius: 10,
              padding: 16,
            }}
          >
            <div style={{ display: 'grid', gap: 4 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
                Hits
              </span>
              <span
                data-testid="keno-hits"
                style={{ fontVariantNumeric: 'tabular-nums', color: GREEN }}
              >
                {hits} / {resultPicks.length}
              </span>
            </div>

            <div style={{ display: 'grid', gap: 4 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
                Drawn
              </span>
              <div
                data-testid="keno-drawn"
                style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}
              >
                {(drawn ?? []).map((n) => (
                  <span
                    key={n}
                    style={{
                      ...drawnChipStyle,
                      ...(resultPicks.includes(n) ? drawnHitStyle : null),
                    }}
                  >
                    {n}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}

        {bet && <ResultPanel bet={bet} />}
      </div>
    </section>
  );
}

const drawnChipStyle: React.CSSProperties = {
  font: 'inherit',
  fontWeight: 700,
  padding: '6px 10px',
  borderRadius: 8,
  border: `1px solid ${BORDER}`,
  background: FIELD,
  color: TEXT,
  fontVariantNumeric: 'tabular-nums',
};

const drawnHitStyle: React.CSSProperties = {
  background: GREEN,
  color: '#06210f',
  borderColor: GREEN,
};

function riskStyle(active: boolean): React.CSSProperties {
  return {
    font: 'inherit',
    fontWeight: 600,
    flex: 1,
    padding: '8px 0',
    borderRadius: 8,
    border: `1px solid ${active ? GREEN : BORDER}`,
    background: active ? 'rgba(27,217,106,0.15)' : '#1b2230',
    color: active ? GREEN : '#cdd3e0',
    cursor: 'pointer',
  };
}

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
