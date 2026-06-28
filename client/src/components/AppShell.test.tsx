import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SessionProvider } from '../lib/session';
import type {
  ActionResult,
  CrashSubscription,
  FairnessResult,
  GameClient,
  GameStateProjection,
} from '../lib/transport';
import { GameClientProvider } from '../lib/transport';
import { AppShell } from './AppShell';

// A fully-typed stub GameClient (no Math.random — fixed values) so we can drive
// `isDemo` and the server-authoritative balance without a real backend.
function makeStubClient(opts: {
  isDemo: boolean;
  balanceMinor: number;
}): GameClient {
  let calls = 0;
  return {
    isDemo: opts.isDemo,
    async startGuestSession() {
      return {
        userId: 'u1',
        walletId: 'w1',
        currency: 'GOLD',
        mode: 'PLAY',
        tokens: {
          accessToken: 'a',
          refreshToken: 'r',
          tokenType: 'bearer',
        },
      };
    },
    async me() {
      calls += 1;
      return {
        userId: 'u1',
        walletId: 'w1',
        currency: 'GOLD',
        mode: 'PLAY',
        // proves refreshBalance re-fetches: first 500000, then 750000.
        balanceMinor: calls === 1 ? opts.balanceMinor : 750000,
      };
    },
    async bet() {
      throw new Error('not used');
    },
    async spin() {
      throw new Error('not used');
    },
    async action(): Promise<ActionResult> {
      throw new Error('not used');
    },
    async state(): Promise<GameStateProjection> {
      throw new Error('not used');
    },
    async fairness(): Promise<FairnessResult> {
      return { available: false, reason: 'stub' };
    },
    crashSocket(): CrashSubscription {
      return { close: () => undefined };
    },
  };
}

function renderShell(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <MemoryRouter>
          <AppShell />
        </MemoryRouter>
      </SessionProvider>
    </GameClientProvider>,
  );
}

describe('AppShell', () => {
  it('always shows the persistent play-money badge', async () => {
    renderShell(makeStubClient({ isDemo: false, balanceMinor: 500000 }));
    expect(screen.getByText(/not real money/i)).toBeInTheDocument();
    // flush the async session bootstrap so no state update escapes act()
    await screen.findByText(/5000\.00/);
  });

  it('shows the DEMO banner only when the client is the demo transport', async () => {
    const { unmount } = renderShell(
      makeStubClient({ isDemo: true, balanceMinor: 500000 }),
    );
    expect(screen.getByText(/outcomes are mocked/i)).toBeInTheDocument();
    await screen.findByText(/5000\.00/);
    unmount();

    renderShell(makeStubClient({ isDemo: false, balanceMinor: 500000 }));
    expect(screen.queryByText(/outcomes are mocked/i)).not.toBeInTheDocument();
    await screen.findByText(/5000\.00/);
  });

  it('bootstraps the server-authoritative balance into the badge', async () => {
    renderShell(makeStubClient({ isDemo: false, balanceMinor: 500000 }));
    // 500000 minor GOLD => "5000.00", fetched from /me (never computed client-side).
    expect(await screen.findByText(/5000\.00/)).toBeInTheDocument();
  });
});
