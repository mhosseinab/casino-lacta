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
import LimboView from './LimboView';

// A real `originals.limbo` settled BetObject — its `outcome` is the engine's
// `Limbo.play` detail (`generated/target/won`) flattened with the bet-loop's
// `multiplier` (= target on a win) + server-stamped `payoutMinor`
// (engine/games/limbo.py + app/games/bet_loop.py). Rendered VERBATIM.
const limboBet: BetObject = {
  betId: 'bet-limbo-1',
  gameId: 'originals.limbo',
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'SETTLED',
  fairness: { serverSeedHash: 'hash-abc', clientSeed: 'cs-1', nonce: 7 },
  input: { target: 2.0 },
  outcome: {
    generated: 3.71,
    target: 2.0,
    won: true,
    multiplier: 2.0,
    payoutMinor: 200,
  },
  createdAt: null,
  settledAt: null,
  idempotencyKeys: { settle: 'k1' },
};

class FakeGameClient implements GameClient {
  readonly isDemo = false;
  betCalls: Array<{ gameId: string; input: BetRequest }> = [];
  fairnessCalledWith: string | null = null;
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
    return this.opts.betResult ?? limboBet;
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
    this.fairnessCalledWith = betId;
    const disclosure: FairnessDisclosure = {
      betId,
      gameId: 'originals.limbo',
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

function renderLimbo(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <LimboView />
      </SessionProvider>
    </GameClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('LimboView — thin renderer of a server-decided limbo bet', () => {
  it('sends the chosen target as bet input (never an outcome)', async () => {
    const client = new FakeGameClient({ betResult: limboBet });
    renderLimbo(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    await waitFor(() => expect(client.betCalls).toHaveLength(1));
    expect(client.betCalls[0].gameId).toBe('originals.limbo');
    expect(client.betCalls[0].input.input).toEqual({ target: 2.0 });
  });

  it('renders the server multiplier, payout (formatted) and generated VERBATIM', async () => {
    const client = new FakeGameClient({ betResult: limboBet });
    renderLimbo(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    // multiplier (2×) + generated (3.71) come straight off outcome; payout is the
    // server payoutMinor formatted via formatMinor (200 -> "2.00"). Scope to the
    // result panel — "2×" also appears as a BetControls quick-stake chip.
    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText(/2×/)).toBeInTheDocument();
    expect(result.getByText('2.00')).toBeInTheDocument();
    expect(screen.getByTestId('limbo-result')).toHaveTextContent('3.71');
  });

  it('renders a LOSING result verbatim — 0× / 0.00 payout, generated shown, no win credit', async () => {
    // A real losing settled bet: generated 1.42 < target 2.0 → won:false,
    // multiplier 0, payoutMinor 0. The thin renderer must show the loss exactly,
    // never granting a phantom credit. This is the trust-boundary branch.
    const losingBet: BetObject = {
      ...limboBet,
      betId: 'bet-limbo-loss',
      outcome: {
        generated: 1.42,
        target: 2.0,
        won: false,
        multiplier: 0,
        payoutMinor: 0,
      },
    };
    const client = new FakeGameClient({ betResult: losingBet });
    renderLimbo(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText('0×')).toBeInTheDocument();
    expect(result.getByText('0.00')).toBeInTheDocument();
    // Generated value shown verbatim; loss framing ("Crashed at"), not "Won at".
    expect(screen.getByTestId('limbo-result')).toHaveTextContent('1.42');
    expect(screen.getByTestId('limbo-result')).toHaveTextContent(/crashed at/i);
    expect(screen.queryByTestId('limbo-result')).not.toHaveTextContent(
      /won at/i,
    );
  });

  it('shows the preview win chance for the target (49.50% at 2.00×, edge 1%)', () => {
    const client = new FakeGameClient();
    renderLimbo(client);
    expect(screen.getByText('49.50 %')).toBeInTheDocument();
  });

  it('re-fetches the server balance after a settle (never computes it)', async () => {
    const client = new FakeGameClient({ betResult: limboBet });
    renderLimbo(client);
    await waitFor(() => expect(client.meCalls).toBe(1));

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));
    await screen.findByTestId('limbo-result');

    await waitFor(() => expect(client.meCalls).toBe(2));
  });

  it('surfaces a server bet-rejection reason and renders no result', async () => {
    const client = new FakeGameClient({
      betError: new BetRejectedError(
        403,
        'bet blocked: max multiplier exceeded',
      ),
    });
    renderLimbo(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/max multiplier exceeded/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('limbo-result')).not.toBeInTheDocument();
  });

  it('surfaces a NEUTRAL message on a non-rejection transport fault', async () => {
    const client = new FakeGameClient({
      betError: new Error('Internal Server Error'),
    });
    renderLimbo(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/something went wrong/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/internal server error/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('limbo-result')).not.toBeInTheDocument();
  });

  it('(reduced motion) renders the static generated value immediately', async () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );

    const client = new FakeGameClient({ betResult: limboBet });
    renderLimbo(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    const result = await screen.findByTestId('limbo-result');
    expect(result).toHaveTextContent('3.71');
    expect(screen.getByTestId('limbo-meter')).toHaveAttribute(
      'data-static',
      'true',
    );
  });
});
