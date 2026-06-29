import type { BetObject } from '../contracts';
import { formatMinor } from '../lib/money';
import { FairnessDrawer } from './FairnessDrawer';

// Renders a settled bet's SERVER result verbatim (plan §3). The server decides every
// outcome and balance; this is a thin renderer that only reads + formats. It never
// recomputes a payout or multiplier — both come straight off `outcome` — and the "Verify"
// affordance is the S7 FairnessDrawer wired to this bet's betId.
//
// `outcome` is the contract's open map ({[key]: unknown}); we read the server-stamped
// keys (cast at the read) — we never recompute or assign a payout/multiplier.
export function ResultPanel(props: { bet: BetObject }) {
  const { bet } = props;
  const multiplier = bet.outcome.multiplier as number;
  // Read-only: the server-stamped payout, never a local computation.
  const payout = bet.outcome.payoutMinor as number;

  return (
    <div
      aria-label="Bet result"
      style={{
        display: 'grid',
        gap: 6,
        padding: 12,
        borderRadius: 8,
        border: '1px solid rgba(127,127,127,0.4)',
        maxWidth: 360,
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <strong>{bet.status}</strong>
        <span>{multiplier}×</span>
      </div>
      <div
        style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}
      >
        <span style={{ opacity: 0.7 }}>Payout</span>
        <span style={{ fontWeight: 700 }}>
          {formatMinor(payout, bet.currency)}{' '}
          <span style={{ fontSize: 12, fontWeight: 500, opacity: 0.7 }}>
            {bet.currency}
          </span>
        </span>
      </div>
      <FairnessDrawer betId={bet.betId} />
    </div>
  );
}
