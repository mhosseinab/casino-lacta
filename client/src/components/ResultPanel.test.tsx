import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { BetObject } from '../contracts';
import type { FairnessResult, GameClient } from '../lib/transport';
import { GameClientProvider } from '../lib/transport';
import { ResultPanel } from './ResultPanel';

// A real-shaped settled BetObject (field names from contracts BetObject /
// app/games/bet_loop.py). outcome carries the server-decided multiplier + payoutMinor;
// ResultPanel renders them VERBATIM and never recomputes a payout.
const FIXTURE: BetObject = {
  betId: 'bet_abc123',
  gameId: 'originals.dice',
  userId: 'user_1',
  walletId: 'wallet_1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'WON',
  fairness: {
    serverSeedHash: 'abc',
    clientSeed: 'seed',
    nonce: 7,
  },
  input: { target: 50, direction: 'UNDER' },
  outcome: { roll: 42.31, win: true, multiplier: 1.98, payoutMinor: 198 },
  createdAt: null,
  settledAt: null,
  idempotencyKeys: {},
};

// Minimal GameClient stub; only fairness() matters here (it backs the embedded
// FairnessDrawer's "Verify this bet" trigger). Pattern mirrors FairnessDrawer.test.tsx.
function makeClient(fairness: () => Promise<FairnessResult>): GameClient {
  return {
    isDemo: false,
    async startGuestSession() {
      throw new Error('not used');
    },
    async me() {
      throw new Error('not used');
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
    fairness,
    crashSocket() {
      return { close: () => undefined };
    },
  };
}

function renderPanel(bet: BetObject, client?: GameClient) {
  return render(
    <GameClientProvider
      client={
        client ?? makeClient(async () => ({ available: false, reason: 'n/a' }))
      }
    >
      <ResultPanel bet={bet} />
    </GameClientProvider>,
  );
}

describe('ResultPanel', () => {
  it('renders the server status, multiplier, and formatted payout verbatim', () => {
    renderPanel(FIXTURE);

    expect(screen.getByText(/WON/)).toBeInTheDocument();
    // multiplier straight from outcome.multiplier (1.98×), payout via formatMinor
    // (198 minor -> "1.98"). No recomputation.
    expect(screen.getByText(/1\.98×/)).toBeInTheDocument();
    expect(screen.getByText('1.98')).toBeInTheDocument();
  });

  it('renders a loss status with the server payout (0.00) unchanged', () => {
    const lost: BetObject = {
      ...FIXTURE,
      status: 'LOST',
      outcome: { roll: 88.1, win: false, multiplier: 0, payoutMinor: 0 },
    };
    renderPanel(lost);

    expect(screen.getByText(/LOST/)).toBeInTheDocument();
    expect(screen.getByText('0.00')).toBeInTheDocument();
  });

  it('wires the bet betId into the FairnessDrawer Verify trigger', async () => {
    const fairness = vi.fn(
      async (): Promise<FairnessResult> => ({
        available: false,
        reason: 'n/a',
      }),
    );
    renderPanel(FIXTURE, makeClient(fairness));

    await userEvent.click(
      screen.getByRole('button', { name: /verify this bet/i }),
    );

    expect(fairness).toHaveBeenCalledWith(FIXTURE.betId);
  });
});
