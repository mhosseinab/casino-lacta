import { BrowserRouter } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { SessionProvider } from './lib/session';
import { GameClientProvider } from './lib/transport';

// Composition root (presentation only — no outcome, RNG, payout, or balance logic
// ever lives in the client; see CLAUDE.md iron rules). The transport seam picks the
// real Http or quarantined demo Mock by config; the session store bootstraps the
// guest + server balance; the router renders the shell.
export function App() {
  return (
    <GameClientProvider>
      <SessionProvider>
        <BrowserRouter>
          <AppShell />
        </BrowserRouter>
      </SessionProvider>
    </GameClientProvider>
  );
}
