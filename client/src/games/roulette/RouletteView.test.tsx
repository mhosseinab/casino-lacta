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
import RouletteView from './RouletteView';

// A real `originals.roulette` settled BetObject — its `outcome` is the engine's
// `Roulette99.play` detail (`result/colour/settlements`) flattened with the bet
// loop's aggregate `multiplier` + server-stamped `payoutMinor`
// (engine/games/roulette99.py + app/games/bet_loop.py). Rendered VERBATIM.
// A BLACK bet that landed BLACK (50 pockets → 0.99/0.50 = 1.98× exactly).
const rouletteBet: BetObject = {
  betId: 'bet-roulette-1',
  gameId: 'originals.roulette',
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'SETTLED',
  fairness: { serverSeedHash: 'hash-abc', clientSeed: 'cs-1', nonce: 7 },
  input: { bets: [{ value: 'BLACK', stakeMinor: 100 }] },
  outcome: {
    result: 73,
    colour: 'BLACK',
    settlements: [{ bet: 0, value: 'BLACK', won: true, multiplier: 1.98 }],
    multiplier: 1.98,
    payoutMinor: 198,
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
    return this.opts.betResult ?? rouletteBet;
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
      gameId: 'originals.roulette',
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

function renderRoulette(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <RouletteView />
      </SessionProvider>
    </GameClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('RouletteView — thin renderer of a server-decided roulette bet', () => {
  it('sends the SELECTED colour as a single bet, stake mirrored top-level + per-bet', async () => {
    const client = new FakeGameClient({ betResult: rouletteBet });
    renderRoulette(client);

    // Pick a NON-default colour to prove the selection→input wiring (not a hardcode).
    await userEvent.click(screen.getByRole('button', { name: /green/i }));
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    await waitFor(() => expect(client.betCalls).toHaveLength(1));
    const { gameId, input } = client.betCalls[0];
    expect(gameId).toBe('originals.roulette');
    // v1 single colour-pick: exactly one bet whose stake equals the top-level stake.
    expect(input.input).toEqual({
      bets: [{ value: 'GREEN', stakeMinor: 100 }],
    });
    const bets = (input.input as { bets: Array<{ stakeMinor: number }> }).bets;
    expect(bets[0].stakeMinor).toBe(input.stakeMinor);
  });

  it('renders the server multiplier, payout (formatted), result + colour VERBATIM', async () => {
    const client = new FakeGameClient({ betResult: rouletteBet });
    renderRoulette(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    // multiplier (1.98×) + payout (198 → "1.98") come straight off outcome. Scope
    // the multiplier to the result panel (a "2×" quick-stake chip lives elsewhere).
    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText('1.98×')).toBeInTheDocument();
    expect(result.getByText('1.98')).toBeInTheDocument();
    // The spun result number + colour, shown verbatim from the server.
    const spun = screen.getByTestId('roulette-result');
    expect(spun).toHaveTextContent('73');
    expect(spun).toHaveTextContent(/black/i);
  });

  it('renders a LOSING result verbatim — 0× / 0.00 payout, no phantom credit', async () => {
    // Bet BLACK, the wheel landed RED 23 → won:false, multiplier 0, payout 0.
    const losingBet: BetObject = {
      ...rouletteBet,
      betId: 'bet-roulette-loss',
      outcome: {
        result: 23,
        colour: 'RED',
        settlements: [{ bet: 0, value: 'BLACK', won: false, multiplier: 0 }],
        multiplier: 0,
        payoutMinor: 0,
      },
    };
    const client = new FakeGameClient({ betResult: losingBet });
    renderRoulette(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText('0×')).toBeInTheDocument();
    expect(result.getByText('0.00')).toBeInTheDocument();
    // The losing colour/result shown verbatim — never a credit.
    const spun = screen.getByTestId('roulette-result');
    expect(spun).toHaveTextContent('23');
    expect(spun).toHaveTextContent(/red/i);
  });

  it('shows the preview win chance for each colour (49 / 50 / ~1%)', async () => {
    const client = new FakeGameClient();
    renderRoulette(client);
    const chance = () => screen.getByTestId('roulette-winchance');

    // Default selection is BLACK (50 pockets).
    expect(chance()).toHaveTextContent('50.00 %');

    await userEvent.click(screen.getByRole('button', { name: /red/i }));
    expect(chance()).toHaveTextContent('49.00 %');

    await userEvent.click(screen.getByRole('button', { name: /green/i }));
    expect(chance()).toHaveTextContent('1.00 %');
  });

  it('re-fetches the server balance after a settle (never computes it)', async () => {
    const client = new FakeGameClient({ betResult: rouletteBet });
    renderRoulette(client);
    await waitFor(() => expect(client.meCalls).toBe(1));

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));
    await screen.findByTestId('roulette-result');

    await waitFor(() => expect(client.meCalls).toBe(2));
  });

  it('surfaces a server bet-rejection reason and renders no result', async () => {
    const client = new FakeGameClient({
      betError: new BetRejectedError(403, 'bet blocked: max bet exceeded'),
    });
    renderRoulette(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(await screen.findByText(/max bet exceeded/i)).toBeInTheDocument();
    expect(screen.queryByTestId('roulette-result')).not.toBeInTheDocument();
  });

  it('surfaces a NEUTRAL message on a non-rejection transport fault', async () => {
    const client = new FakeGameClient({
      betError: new Error('Internal Server Error'),
    });
    renderRoulette(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/something went wrong/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/internal server error/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('roulette-result')).not.toBeInTheDocument();
  });

  it('(reduced motion) renders the static result immediately', async () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );

    const client = new FakeGameClient({ betResult: rouletteBet });
    renderRoulette(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    const spun = await screen.findByTestId('roulette-result');
    expect(spun).toHaveTextContent('73');
    expect(screen.getByTestId('roulette-stage')).toHaveAttribute(
      'data-static',
      'true',
    );
  });
});
