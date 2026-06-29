import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  BetObject,
  BetRequest,
  FairnessDisclosure,
  GuestSessionResponse,
  MeResponse,
} from '../../contracts';
import { SessionProvider } from '../../lib/session';
import {
  BetRejectedError,
  type CrashSocketHandlers,
  type CrashSubscription,
  type FairnessResult,
  type GameClient,
  GameClientProvider,
  type GameStateProjection,
} from '../../lib/transport';
import PlinkoView from './PlinkoView';

// Build a server `path` of the requested length with exactly `bin` right bounces so
// the fixture can never drift from its bin (path.filter(R).length === bin === rows R).
function pathFor(rows: number, bin: number): ('L' | 'R')[] {
  return [
    ...Array<'L' | 'R'>(bin).fill('R'),
    ...Array<'L' | 'R'>(rows - bin).fill('L'),
  ];
}

// A real `originals.plinko` settled BetObject — its `outcome` is the engine's
// Plinko.play detail (rows/risk/bin/rightBounces/path) flattened with the bet-loop's
// server-decided `multiplier` + `payoutMinor`. Rendered VERBATIM by the thin client.
const plinkoBet: BetObject = {
  betId: 'bet-plinko-1',
  gameId: 'originals.plinko',
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'SETTLED',
  fairness: { serverSeedHash: 'hash-abc', clientSeed: 'cs-1', nonce: 7 },
  input: { rows: 12, risk: 'MEDIUM' },
  outcome: {
    // Real engine table value: rows=12 MEDIUM bin 8 → 1.12× (registry _PLINKO_TABLES).
    rows: 12,
    risk: 'MEDIUM',
    bin: 8,
    rightBounces: 8,
    path: pathFor(12, 8),
    multiplier: 1.12,
    payoutMinor: 112, // floor(100 × 1.12)
  },
  createdAt: null,
  settledAt: null,
  idempotencyKeys: { settle: 'k1' },
};

class FakeGameClient implements GameClient {
  readonly isDemo = false;
  betCalls: Array<{ gameId: string; input: BetRequest }> = [];
  meCalls = 0;
  private balanceMinor = 1_000_000;

  constructor(
    private readonly opts: { betResult?: BetObject; betError?: Error } = {},
  ) {}

  async startGuestSession(): Promise<GuestSessionResponse> {
    return {
      userId: 'u1',
      walletId: 'w1',
      currency: 'GOLD',
      mode: 'PLAY',
      tokens: { accessToken: 'a', refreshToken: 'r', tokenType: 'bearer' },
    };
  }

  async me(): Promise<MeResponse> {
    this.meCalls += 1;
    return {
      userId: 'u1',
      walletId: 'w1',
      currency: 'GOLD',
      mode: 'PLAY',
      balanceMinor: this.balanceMinor,
    };
  }

  async bet(gameId: string, input: BetRequest): Promise<BetObject> {
    this.betCalls.push({ gameId, input });
    if (this.opts.betError) throw this.opts.betError;
    return this.opts.betResult ?? plinkoBet;
  }

  async spin(gameId: string, input: BetRequest): Promise<BetObject> {
    return this.bet(gameId, input);
  }

  async action(): Promise<GameStateProjection> {
    return {};
  }

  async state(): Promise<GameStateProjection> {
    return {};
  }

  async fairness(betId: string): Promise<FairnessResult> {
    const disclosure: FairnessDisclosure = {
      betId,
      gameId: 'originals.plinko',
      serverSeedHash: 'hash-abc',
      serverSeed: null,
      revealed: false,
      clientSeed: 'cs-1',
      nonce: 7,
      derivation: 'HMAC-SHA256',
      verifierUrl: `https://verify.example/${betId}`,
    };
    return { available: true, disclosure };
  }

  crashSocket(_handlers: CrashSocketHandlers): CrashSubscription {
    return { close: () => undefined };
  }
}

function renderPlinko(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <PlinkoView />
      </SessionProvider>
    </GameClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('PlinkoView — thin renderer of a server-decided plinko bet', () => {
  it('sends the chosen rows + risk as bet input (never an outcome)', async () => {
    const client = new FakeGameClient({ betResult: plinkoBet });
    renderPlinko(client);

    // Change both selectors away from the defaults to prove the SELECTED values flow.
    await userEvent.click(screen.getByRole('button', { name: '8' }));
    await userEvent.click(screen.getByRole('button', { name: 'HIGH' }));
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    await waitFor(() => expect(client.betCalls).toHaveLength(1));
    expect(client.betCalls[0].gameId).toBe('originals.plinko');
    expect(client.betCalls[0].input.input).toEqual({ rows: 8, risk: 'HIGH' });
  });

  it('renders the server multiplier, payout (formatted) and landing bin VERBATIM', async () => {
    const client = new FakeGameClient({ betResult: plinkoBet });
    renderPlinko(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    // multiplier (1.12×) + payout (112 -> "1.12") come straight off outcome. Scoped to
    // the result panel (within) per the Limbo reference — the board renders its own
    // bin digits, so keep payout/multiplier assertions inside "Bet result".
    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText(/1.12×/)).toBeInTheDocument();
    expect(result.getByText('1.12')).toBeInTheDocument();
    // The server bin is rendered as the authoritative landing.
    expect(screen.getByTestId('plinko-result')).toHaveTextContent('8');
  });

  it('renders a LOW (<1×) result verbatim — server multiplier/payout, no phantom credit', async () => {
    // Plinko multipliers can be <1 but >0 (a centre bin). The thin renderer must show
    // the server value exactly — payout < stake, never a phantom win credit.
    const lowBet: BetObject = {
      ...plinkoBet,
      betId: 'bet-plinko-low',
      outcome: {
        // Real engine table value: rows=12 MEDIUM centre bin 6 → 0.65× (a sub-1× loss).
        rows: 12,
        risk: 'MEDIUM',
        bin: 6,
        rightBounces: 6,
        path: pathFor(12, 6),
        multiplier: 0.65,
        payoutMinor: 65, // floor(100 × 0.65) < 100 stake
      },
    };
    const client = new FakeGameClient({ betResult: lowBet });
    renderPlinko(client);

    await waitFor(() => expect(client.meCalls).toBe(1));
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText(/0.65×/)).toBeInTheDocument(); // server multiplier
    expect(result.getByText('0.65')).toBeInTheDocument(); // server payout 65 -> 0.65
    expect(screen.getByTestId('plinko-result')).toHaveTextContent('6');
    // Server re-sync after settle — the balance is re-fetched, never locally credited.
    await waitFor(() => expect(client.meCalls).toBe(2));
  });

  it('re-fetches the server balance after a settle (never computes it)', async () => {
    const client = new FakeGameClient({ betResult: plinkoBet });
    renderPlinko(client);
    await waitFor(() => expect(client.meCalls).toBe(1));

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));
    await screen.findByTestId('plinko-result');

    await waitFor(() => expect(client.meCalls).toBe(2));
  });

  it('surfaces a server bet-rejection reason and renders no result', async () => {
    const client = new FakeGameClient({
      betError: new BetRejectedError(403, 'bet blocked: max bet exceeded'),
    });
    renderPlinko(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(await screen.findByText(/max bet exceeded/i)).toBeInTheDocument();
    expect(screen.queryByTestId('plinko-result')).not.toBeInTheDocument();
  });

  it('surfaces a NEUTRAL message on a non-rejection transport fault', async () => {
    const client = new FakeGameClient({
      betError: new Error('Internal Server Error'),
    });
    renderPlinko(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/something went wrong/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/internal server error/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('plinko-result')).not.toBeInTheDocument();
  });

  it('(reduced motion) lands the ball in the final bin immediately', async () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );

    const client = new FakeGameClient({ betResult: plinkoBet });
    renderPlinko(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(await screen.findByTestId('plinko-result')).toHaveTextContent('8');
    expect(screen.getByTestId('plinko-board')).toHaveAttribute(
      'data-static',
      'true',
    );
    expect(screen.getByTestId('plinko-ball')).toHaveAttribute('data-bin', '8');
  });
});
