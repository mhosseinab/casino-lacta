import { useState } from 'react';
import { BetControls } from '../../components/BetControls';
import { ResultPanel } from '../../components/ResultPanel';
import type { BetObject } from '../../contracts';
import { formatMinor } from '../../lib/money';
import { useSession } from '../../lib/session';
import { BetRejectedError, useGameClient } from '../../lib/transport';
import { RouletteWheel } from './RouletteWheel';
import {
  POCKETS,
  type RouletteColour,
  profitMinor,
  winChancePct,
} from './rouletteMath';

// RouletteView — the colour-pick Original (0–99, NOT a European wheel), built on the
// S9 Dice / S11 Limbo reference pattern. THIN renderer: it sends intent (a single
// colour pick + stake) through the GameClient seam and renders the server's BetObject
// VERBATIM. It computes no outcome, payout, or balance — the spun result/colour,
// aggregate multiplier and payout all come off `bet.outcome`, and the post-settle
// balance is RE-FETCHED from /me.
//
// The win-chance / potential-profit shown are a pre-bet QUOTE (rouletteMath.ts); the
// authoritative multiplier & payout are always the server's `bet.outcome` values
// (rendered by ResultPanel). The quote's only economy input is ROULETTE_PREVIEW_EDGE.
const GAME_ID = 'originals.roulette';

const BG = '#0f1320';
const PANEL = '#171c28';
const FIELD = '#0c1018';
const BORDER = '#222a38';
const GREEN = '#1bd96a';
const TEXT = '#e6e9ef';
const MUTED = '#8b93a7';

// The three picks, in display order, with their swatch colour (cosmetic).
const PICKS: ReadonlyArray<{ colour: RouletteColour; swatch: string }> = [
  { colour: 'RED', swatch: '#e0364f' },
  { colour: 'GREEN', swatch: '#1bd96a' },
  { colour: 'BLACK', swatch: '#9aa3b5' },
];

export default function RouletteView() {
  const client = useGameClient();
  const { balanceMinor, currency, refreshBalance } = useSession();

  // BLACK (50 pockets) is the default pick — the most likely colour.
  const [colour, setColour] = useState<RouletteColour>('BLACK');
  const [stakeMinor, setStakeMinor] = useState<number | null>(100);
  const [bet, setBet] = useState<BetObject | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  // Neutral transport-fault notice, distinct from a server `rejectionReason`.
  const [transportError, setTransportError] = useState<string | null>(null);

  // Pre-bet quote (preview). Authoritative values arrive on the server result.
  const winPct = winChancePct(colour);
  const profit = stakeMinor != null ? profitMinor(stakeMinor, colour) : 0;

  async function placeBet(stake: number): Promise<void> {
    setRejection(null);
    setTransportError(null);
    try {
      // betId is a client-generated IDEMPOTENCY key, NOT entropy — the server
      // derives the spin from its own seeds. v1 = a SINGLE colour pick whose
      // per-bet stake mirrors the top-level stake (so the aggregate is consistent).
      const result = await client.bet(GAME_ID, {
        betId: crypto.randomUUID(),
        stakeMinor: stake,
        currency,
        mode: 'PLAY',
        input: { bets: [{ value: colour, stakeMinor: stake }] },
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

        {/* Colour pick — three buttons with their pocket counts. The SELECTED colour
            is the only thing sent to the server (it decides the spin). */}
        <div style={{ display: 'grid', gap: 4 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
            Colour
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            {PICKS.map(({ colour: c, swatch }) => (
              <button
                key={c}
                type="button"
                aria-pressed={colour === c}
                onClick={() => setColour(c)}
                style={pickStyle(swatch, colour === c)}
              >
                <span style={{ fontWeight: 800 }}>{c}</span>
                <span style={{ fontSize: 11, opacity: 0.85 }}>
                  {POCKETS[c]} {POCKETS[c] === 1 ? 'pocket' : 'pockets'}
                </span>
              </button>
            ))}
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
            data-testid="roulette-error"
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
        <RouletteWheel
          result={bet ? (bet.outcome.result as number) : null}
          colour={bet ? (bet.outcome.colour as RouletteColour) : null}
          betId={bet?.betId}
        />

        {/* Quote stats — Colour | Win Chance (both derived/preview). */}
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
          <Stat label="Colour">
            <span style={fieldStyle}>{colour}</span>
          </Stat>
          <Stat label="Win Chance">
            <span data-testid="roulette-winchance" style={fieldStyle}>
              {winPct.toFixed(2)} %
            </span>
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

function pickStyle(swatch: string, active: boolean): React.CSSProperties {
  return {
    font: 'inherit',
    flex: 1,
    display: 'grid',
    gap: 2,
    padding: '8px 4px',
    borderRadius: 8,
    border: `2px solid ${active ? swatch : '#222a38'}`,
    background: active ? `${swatch}22` : '#0c1018',
    color: '#e6e9ef',
    cursor: 'pointer',
    textAlign: 'center',
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
