import { formatMinor } from '../lib/money';

// A pure presentational view of the SERVER balance. It takes the already-fetched
// minor-units balance + currency as props (the session store owns the fetch) and
// formats for display ONLY (CLAUDE.md "format only at display"). It performs no
// money math and never derives a balance — null renders a load placeholder.
export function BalanceBadge(props: {
  balanceMinor: number | null;
  currency: string;
}) {
  const display =
    props.balanceMinor === null
      ? '…'
      : formatMinor(props.balanceMinor, props.currency);
  return (
    <span
      aria-label="Balance"
      style={{
        display: 'inline-flex',
        alignItems: 'baseline',
        gap: 4,
        fontVariantNumeric: 'tabular-nums',
        fontWeight: 700,
      }}
    >
      <span>{display}</span>
      <span style={{ fontSize: 12, fontWeight: 500, opacity: 0.7 }}>
        {props.currency}
      </span>
    </span>
  );
}
