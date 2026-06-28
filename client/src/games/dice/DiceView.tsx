import { useState } from 'react';
import { BetControls } from '../../components/BetControls';
import { ResultPanel } from '../../components/ResultPanel';
import type { BetObject } from '../../contracts';
import { useSession } from '../../lib/session';
import { BetRejectedError, useGameClient } from '../../lib/transport';
import { DiceStage } from './DiceStage';

// DiceView — the canonical Original view and the REFERENCE PATTERN for S11–S24.
// It is a THIN renderer: it sends intent (stake + target/direction) through the
// GameClient seam, then renders the server's BetObject VERBATIM. It computes no
// outcome, payout, or balance — the roll/multiplier/payout all come off the server
// result, and the post-settle balance is RE-FETCHED from /me (never derived here).
//
// `target`/`direction` are dice INPUT (not money), so a fractional target is fine;
// the stake is validated to integer minor units by BetControls. The server is the
// authority on min/max-bet + RG limits — a refusal arrives as BetRejectedError and
// is surfaced to the player via BetControls' `rejectionReason`.
const GAME_ID = 'originals.dice';

type Direction = 'UNDER' | 'OVER';

export default function DiceView() {
  const client = useGameClient();
  const { balanceMinor, currency, refreshBalance } = useSession();

  const [target, setTarget] = useState(50);
  const [direction, setDirection] = useState<Direction>('UNDER');
  const [bet, setBet] = useState<BetObject | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);

  async function placeBet(stakeMinor: number): Promise<void> {
    setRejection(null);
    try {
      // betId is a client-generated IDEMPOTENCY key, NOT outcome entropy — the
      // server derives the roll from its own seeds (crypto.randomUUID, never
      // Math.random/getRandomValues).
      const result = await client.bet(GAME_ID, {
        betId: crypto.randomUUID(),
        stakeMinor,
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
      throw err;
    }
  }

  return (
    <section style={{ display: 'grid', gap: 16, padding: 24, maxWidth: 720 }}>
      <h2 style={{ margin: 0 }}>Dice</h2>

      <div
        style={{
          display: 'flex',
          gap: 24,
          flexWrap: 'wrap',
          alignItems: 'flex-start',
        }}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <label style={{ display: 'grid', gap: 4 }}>
            <span style={{ fontSize: 12, fontWeight: 600, opacity: 0.7 }}>
              Target (0–100)
            </span>
            <input
              type="number"
              aria-label="Target"
              min={0}
              max={100}
              step={0.01}
              value={target}
              onChange={(e) => setTarget(Number(e.target.value))}
              style={{
                font: 'inherit',
                padding: '6px 10px',
                borderRadius: 6,
                border: '1px solid rgba(127,127,127,0.4)',
              }}
            />
          </label>

          <fieldset
            style={{
              display: 'flex',
              gap: 8,
              border: 'none',
              padding: 0,
              margin: 0,
            }}
          >
            {(['UNDER', 'OVER'] as const).map((d) => (
              <button
                key={d}
                type="button"
                aria-pressed={direction === d}
                onClick={() => setDirection(d)}
                style={{
                  font: 'inherit',
                  padding: '6px 12px',
                  borderRadius: 6,
                  cursor: 'pointer',
                  border: '1px solid currentColor',
                  background: direction === d ? 'currentColor' : 'transparent',
                  color: direction === d ? 'Canvas' : 'inherit',
                }}
              >
                {d}
              </button>
            ))}
          </fieldset>

          <BetControls
            onBet={(stakeMinor) => void placeBet(stakeMinor)}
            currency={currency}
            balanceMinor={balanceMinor}
            rejectionReason={rejection}
          />
        </div>

        {bet && (
          <div style={{ display: 'grid', gap: 12 }}>
            <DiceStage roll={bet.outcome.roll as number} />
            <ResultPanel bet={bet} />
          </div>
        )}
      </div>
    </section>
  );
}
