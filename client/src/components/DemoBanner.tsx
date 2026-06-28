// Shown ONLY when the active transport is the quarantined demo Mock
// (useGameClient().isDemo). It warns that outcomes are fabricated client-side and
// carry no provably-fair proof — so a demo deploy can never be mistaken for the
// real, server-verified game (CLAUDE.md "the server decides — and proves — outcomes").
export function DemoBanner() {
  return (
    <div
      style={{
        padding: '6px 12px',
        textAlign: 'center',
        fontSize: 13,
        fontWeight: 600,
        color: '#7c2d12',
        background: '#ffedd5',
        borderBottom: '1px solid #fdba74',
      }}
    >
      DEMO — outcomes are mocked, not server-verified
    </div>
  );
}
