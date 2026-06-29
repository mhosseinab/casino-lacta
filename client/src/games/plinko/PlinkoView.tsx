import { useState } from 'react';
import { BetControls } from '../../components/BetControls';
import { ResultPanel } from '../../components/ResultPanel';
import type { BetObject } from '../../contracts';
import { useSession } from '../../lib/session';
import { BetRejectedError, useGameClient } from '../../lib/transport';
import { PlinkoBoard } from './PlinkoBoard';
import type { Bounce } from './plinkoMath';

// PlinkoView — an instant Original built on the S9 Dice / S11 Limbo reference. THIN
// renderer: it sends intent (stake + board shape: rows + risk) through the GameClient
// seam and renders the server's BetObject VERBATIM. It computes no outcome, payout, or
// balance — the bin / path / multiplier / payout all come off `bet.outcome`, and the
// post-settle balance is RE-FETCHED from /me.
//
// DELIBERATELY NO multiplier/profit PREVIEW: unlike Dice/Limbo (whose payout multiplier
// is a closed-form function of the player's own target), the Plinko multiplier table is
// SERVER-OWNED per (rows, risk). The client never replicates it or quotes a payout — the
// only multiplier/payout shown is the server's `bet.outcome` (via ResultPanel).
const GAME_ID = 'originals.plinko';

const ROWS_OPTIONS = [8, 12, 16] as const;
const RISK_OPTIONS = ['LOW', 'MEDIUM', 'HIGH'] as const;
type Risk = (typeof RISK_OPTIONS)[number];

const BG = '#0f1320';
const PANEL = '#171c28';
const FIELD = '#0c1018';
const BORDER = '#222a38';
const TEXT = '#e6e9ef';
const MUTED = '#8b93a7';

export default function PlinkoView() {
  const client = useGameClient();
  const { balanceMinor, currency, refreshBalance } = useSession();

  const [rows, setRows] = useState<number>(12);
  const [risk, setRisk] = useState<Risk>('MEDIUM');
  const [bet, setBet] = useState<BetObject | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  // Neutral transport-fault notice, distinct from a server `rejectionReason`.
  const [transportError, setTransportError] = useState<string | null>(null);

  async function placeBet(stake: number): Promise<void> {
    setRejection(null);
    setTransportError(null);
    try {
      // betId is a client-generated IDEMPOTENCY key, NOT entropy — the server
      // derives the bounce path + bin from its own seeds.
      const result = await client.bet(GAME_ID, {
        betId: crypto.randomUUID(),
        stakeMinor: stake,
        currency,
        mode: 'PLAY',
        input: { rows, risk },
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

  // All board inputs come off the SERVER outcome once settled (so the drop matches the
  // server result exactly); before the first bet the board previews the selected shape.
  const boardRows = bet ? (bet.outcome.rows as number) : rows;
  const path = bet ? (bet.outcome.path as Bounce[]) : null;
  const landedBin = bet ? (bet.outcome.bin as number) : null;
  // Display-only colour: compares two server-stamped values (never a balance compute).
  const won = bet
    ? (bet.outcome.payoutMinor as number) > bet.stakeMinor
    : undefined;

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

        {/* Rows selector (board shape — INPUT, decides nothing). */}
        <div style={{ display: 'grid', gap: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
            Rows
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            {ROWS_OPTIONS.map((r) => (
              <button
                key={r}
                type="button"
                aria-pressed={rows === r}
                onClick={() => setRows(r)}
                style={segStyle(rows === r)}
              >
                {r}
              </button>
            ))}
          </div>
        </div>

        {/* Risk selector (board shape — INPUT). The multiplier table is server-side. */}
        <div style={{ display: 'grid', gap: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
            Risk
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            {RISK_OPTIONS.map((r) => (
              <button
                key={r}
                type="button"
                aria-pressed={risk === r}
                onClick={() => setRisk(r)}
                style={segStyle(risk === r)}
              >
                {r}
              </button>
            ))}
          </div>
        </div>

        {transportError !== null && (
          <p
            role="alert"
            data-testid="plinko-error"
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
        <PlinkoBoard
          rows={boardRows}
          path={path}
          bin={landedBin}
          betId={bet?.betId}
          won={won}
        />

        {bet && <ResultPanel bet={bet} />}
      </div>
    </section>
  );
}

const segStyle = (active: boolean): React.CSSProperties => ({
  font: 'inherit',
  fontWeight: 700,
  flex: 1,
  padding: '8px 0',
  borderRadius: 8,
  border: `1px solid ${active ? '#1bd96a' : '#222a38'}`,
  background: active ? '#16351f' : '#0c1018',
  color: active ? '#1bd96a' : '#cdd3e0',
  cursor: 'pointer',
  fontVariantNumeric: 'tabular-nums',
});

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
