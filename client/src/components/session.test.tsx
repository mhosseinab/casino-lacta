import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SessionProvider, useSession } from '../lib/session';
import type { GameClient } from '../lib/transport';
import { GameClientProvider } from '../lib/transport';

// Minimal stub that counts me() calls so we can assert refreshBalance re-fetches
// the SERVER balance (never accumulates locally).
function makeClient(balances: number[]): GameClient {
  let i = 0;
  return {
    isDemo: false,
    async startGuestSession() {
      return {
        userId: 'u1',
        walletId: 'w1',
        currency: 'GOLD',
        mode: 'PLAY',
        tokens: { accessToken: 'a', refreshToken: 'r', tokenType: 'bearer' },
      };
    },
    async me() {
      const balanceMinor = balances[Math.min(i, balances.length - 1)];
      i += 1;
      return {
        userId: 'u1',
        walletId: 'w1',
        currency: 'GOLD',
        mode: 'PLAY',
        balanceMinor,
      };
    },
    async bet() {
      throw new Error('not used');
    },
    async spin() {
      throw new Error('not used');
    },
    async action() {
      throw new Error('not used');
    },
    async state() {
      throw new Error('not used');
    },
    async fairness() {
      return { available: false, reason: 'stub' };
    },
    crashSocket() {
      return { close: () => undefined };
    },
  };
}

function Consumer() {
  const { balanceMinor, currency, refreshBalance } = useSession();
  return (
    <div>
      <span data-testid="bal">{balanceMinor ?? 'loading'}</span>
      <span data-testid="ccy">{currency}</span>
      <button type="button" onClick={() => void refreshBalance()}>
        refresh
      </button>
    </div>
  );
}

describe('SessionProvider', () => {
  it('bootstraps balance + currency from the server, and refreshBalance re-fetches', async () => {
    render(
      <GameClientProvider client={makeClient([100, 250])}>
        <SessionProvider>
          <Consumer />
        </SessionProvider>
      </GameClientProvider>,
    );

    expect(await screen.findByText('100')).toBeInTheDocument();
    expect(screen.getByTestId('ccy')).toHaveTextContent('GOLD');

    await userEvent.click(screen.getByRole('button', { name: 'refresh' }));
    expect(await screen.findByText('250')).toBeInTheDocument();
  });
});
