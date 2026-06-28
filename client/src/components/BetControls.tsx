import { useState } from 'react';
import { parseStakeToMinor } from '../lib/money';

// The shared stake input + Bet trigger (plan §3). A THIN renderer: it validates the
// stake to INTEGER MINOR UNITS at the hostile client boundary (reusing money.ts —
// never its own parser) and hands the integer to a parent-provided `onBet`. It decides
// NOTHING about outcome, payout, or balance — the parent calls GameClient.bet/spin,
// refreshes the balance, and passes any server rejection back down as `rejectionReason`.
//
// Quick-stakes are integer-only (no float ever touches money). The stake field holds a
// bare minor-units digit string, which round-trips through parseStakeToMinor (bare
// digits == minor units). A typed decimal is read as MAJOR units at the currency scale
// ("1.50" -> 150); only sub-scale precision ("1.005") is rejected.
export function BetControls(props: {
  /** Parent runs the actual bet (GameClient.bet/spin) + balance refresh. */
  onBet: (stakeMinor: number) => void;
  /** Currency for stake parsing; GOLD is the only play-money currency. */
  currency?: string;
  /** Available balance, in minor units — drives the "max" quick-stake. The server
   *  stays authoritative on min/max-bet; an over-max stake surfaces via rejectionReason. */
  balanceMinor?: number | null;
  /** A server bet-rejection reason (min/max-bet, RG/limit block) to surface verbatim. */
  rejectionReason?: string | null;
  /** Initial stake in minor units. */
  defaultStakeMinor?: number;
}) {
  const currency = props.currency ?? 'GOLD';
  const [stakeInput, setStakeInput] = useState(
    String(props.defaultStakeMinor ?? 100),
  );
  const [localError, setLocalError] = useState<string | null>(null);

  // The current valid stake in minor units, or null if the field is unparseable.
  // Reused by the quick-stake buttons (integer math only).
  const parsed = parseStakeToMinor(stakeInput, currency);
  const currentMinor = parsed instanceof Error ? null : parsed;

  function setMinor(value: number): void {
    setStakeInput(String(value));
    setLocalError(null);
  }

  function handleBet(): void {
    const result = parseStakeToMinor(stakeInput, currency);
    if (result instanceof Error) {
      setLocalError(result.message);
      return;
    }
    setLocalError(null);
    props.onBet(result);
  }

  const message = localError ?? props.rejectionReason ?? null;

  return (
    <div style={{ display: 'grid', gap: 8, maxWidth: 320 }}>
      <label style={{ display: 'grid', gap: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 600, opacity: 0.7 }}>
          Stake ({currency})
        </span>
        <input
          type="text"
          inputMode="decimal"
          aria-label="Stake"
          value={stakeInput}
          onChange={(e) => {
            setStakeInput(e.target.value);
            setLocalError(null);
          }}
          style={{
            font: 'inherit',
            padding: '6px 10px',
            borderRadius: 6,
            border: '1px solid rgba(127,127,127,0.4)',
            fontVariantNumeric: 'tabular-nums',
          }}
        />
      </label>

      <div style={{ display: 'flex', gap: 6 }}>
        <button
          type="button"
          onClick={() =>
            currentMinor !== null && setMinor(Math.floor(currentMinor / 2))
          }
          disabled={currentMinor === null}
        >
          ½
        </button>
        <button
          type="button"
          onClick={() => currentMinor !== null && setMinor(currentMinor * 2)}
          disabled={currentMinor === null}
        >
          2×
        </button>
        <button
          type="button"
          onClick={() =>
            props.balanceMinor != null && setMinor(props.balanceMinor)
          }
          disabled={props.balanceMinor == null}
        >
          Max
        </button>
      </div>

      <button
        type="button"
        onClick={handleBet}
        style={{
          font: 'inherit',
          fontWeight: 700,
          padding: '8px 16px',
          borderRadius: 6,
          border: '1px solid currentColor',
          background: 'transparent',
          color: 'inherit',
          cursor: 'pointer',
        }}
      >
        Bet
      </button>

      {message !== null && (
        <p role="alert" style={{ margin: 0, color: '#c0392b', fontSize: 13 }}>
          {message}
        </p>
      )}
    </div>
  );
}
