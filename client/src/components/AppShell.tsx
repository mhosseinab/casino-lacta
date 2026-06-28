import { Link, Route, Routes } from 'react-router-dom';
import { useSession } from '../lib/session';
import { useGameClient } from '../lib/transport';
import { BalanceBadge } from './BalanceBadge';
import { DemoBanner } from './DemoBanner';
import { GameRoute } from './GameRoute';
import { Lobby } from './Lobby';
import { PlayMoneyBadge } from './PlayMoneyBadge';

// The app shell: a top bar (app name + balance + persistent play-money badge), the
// demo banner when the mock transport is active, and the router outlet. It owns no
// outcome/balance logic — it reads the server balance from the session store and
// renders it (CLAUDE.md "thin renderer").
export function AppShell() {
  const { isDemo } = useGameClient();
  const { balanceMinor, currency } = useSession();

  return (
    <div style={{ minHeight: '100vh' }}>
      {isDemo ? <DemoBanner /> : null}
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
          padding: '12px 24px',
          borderBottom: '1px solid #1f2937',
        }}
      >
        <h1 style={{ fontSize: 18, margin: 0 }}>
          <Link to="/" style={{ color: 'inherit', textDecoration: 'none' }}>
            casino-lacta
          </Link>
        </h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <PlayMoneyBadge />
          <BalanceBadge balanceMinor={balanceMinor} currency={currency} />
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Lobby />} />
          <Route path="/play/:gameId" element={<GameRoute />} />
        </Routes>
      </main>
    </div>
  );
}
