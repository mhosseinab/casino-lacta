// The persistent, always-visible play-money disclosure (CLAUDE.md load-bearing
// boundary: GOLD is non-redeemable; no prizes, no cash-out). It is NEVER gated on
// transport/mode — it shows on every screen.
export function PlayMoneyBadge() {
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 600,
        color: '#b45309',
        background: '#fef3c7',
        border: '1px solid #fde68a',
        whiteSpace: 'nowrap',
      }}
    >
      Not real money · No prizes
    </span>
  );
}
