import { render, screen, waitFor } from '@testing-library/react';
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
import DiceView from './DiceView';

// A real `originals.dice` settled BetObject — its `outcome` shape is the engine's
// `Dice.play` detail (`roll/target/direction/won`) flattened with the bet-loop's
// `multiplier` + server-stamped `payoutMinor` (app/games/bet_loop.py). The view
// renders these VERBATIM — it computes no outcome or balance.
const diceBet: BetObject = {
  betId: 'bet-2f9c',
  gameId: 'originals.dice',
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'SETTLED',
  fairness: { serverSeedHash: 'hash-abc', clientSeed: 'cs-1', nonce: 7 },
  input: { target: 50, direction: 'UNDER' },
  outcome: {
    roll: 42.13,
    target: 50,
    direction: 'UNDER',
    won: true,
    multiplier: 2.5,
    payoutMinor: 250,
  },
  createdAt: null,
  settledAt: null,
  idempotencyKeys: { settle: 'k1' },
};

// A controllable fake GameClient — records calls and returns/raises on demand. It
// is NOT the MockGameClient (which fabricates outcomes); the view must render only
// what this returns, proving the view decides nothing.
class FakeGameClient implements GameClient {
  readonly isDemo = false;
  betCalls: Array<{ gameId: string; input: BetRequest }> = [];
  fairnessCalledWith: string | null = null;
  meCalls = 0;
  private balanceMinor = 1_000_000;

  constructor(
    private readonly opts: {
      betResult?: BetObject;
      betError?: Error;
    } = {},
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
    return this.opts.betResult ?? diceBet;
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
      gameId: 'originals.dice',
      serverSeedHash: 'hash-abc',
      serverSeed: null,
      revealed: false,
      clientSeed: 'cs-1',
      nonce: 7,
      derivation: 'HMAC-SHA256',
      // Echo the betId so the test can prove the drawer used the RETURNED id.
      verifierUrl: `https://verify.example/${betId}`,
    };
    return { available: true, disclosure };
  }

  crashSocket(_handlers: CrashSocketHandlers): CrashSubscription {
    return { close: () => undefined };
  }
}

function renderDice(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <DiceView />
      </SessionProvider>
    </GameClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('DiceView — thin renderer of a server-decided dice bet', () => {
  it('renders the server multiplier, payout (formatted minor units) and roll VERBATIM', async () => {
    const client = new FakeGameClient({ betResult: diceBet });
    renderDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    // multiplier (2.5×) and roll (42.13) come straight off outcome; payout is the
    // server-stamped payoutMinor formatted via formatMinor (250 -> "2.50").
    expect(await screen.findByText(/2\.5×/)).toBeInTheDocument();
    expect(screen.getByText('2.50')).toBeInTheDocument();
    expect(screen.getByTestId('dice-roll')).toHaveTextContent('42.13');
  });

  it('re-fetches the server balance after a settle (never computes it)', async () => {
    const client = new FakeGameClient({ betResult: diceBet });
    renderDice(client);
    // 1 me() from SessionProvider bootstrap.
    await waitFor(() => expect(client.meCalls).toBe(1));

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));
    await screen.findByTestId('dice-roll');

    // A second me() — the post-settle refreshBalance, not a local balance edit.
    await waitFor(() => expect(client.meCalls).toBe(2));
  });

  it('wires the fairness "Verify" drawer to the RETURNED betId', async () => {
    const client = new FakeGameClient({ betResult: diceBet });
    renderDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));
    await screen.findByTestId('dice-roll');
    await userEvent.click(
      screen.getByRole('button', { name: /verify this bet/i }),
    );

    await waitFor(() => expect(client.fairnessCalledWith).toBe(diceBet.betId));
    const link = await screen.findByRole('link', {
      name: /verifier/i,
    });
    expect(link).toHaveAttribute(
      'href',
      `https://verify.example/${diceBet.betId}`,
    );
  });

  it('rejects a sub-scale (float) stake locally and never places the bet', async () => {
    const client = new FakeGameClient({ betResult: diceBet });
    renderDice(client);

    const stake = screen.getByLabelText('Stake');
    await userEvent.clear(stake);
    await userEvent.type(stake, '1.005');
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(client.betCalls).toHaveLength(0);
    expect(screen.queryByTestId('dice-roll')).not.toBeInTheDocument();
  });

  it('surfaces a server bet-rejection reason and renders no result', async () => {
    const client = new FakeGameClient({
      betError: new BetRejectedError(
        403,
        'bet blocked: daily loss limit reached',
      ),
    });
    renderDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/daily loss limit reached/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('dice-roll')).not.toBeInTheDocument();
  });

  it('surfaces a NEUTRAL message on a non-rejection transport fault and renders no result', async () => {
    // A 500 / network / parse failure arrives as a plain Error (NOT
    // BetRejectedError). It must not become an unhandled rejection: the view
    // shows a generic notice (distinct from a server rejectionReason) and places
    // no result. This propagates to all 15 later game views.
    const client = new FakeGameClient({
      betError: new Error('Internal Server Error'),
    });
    renderDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/something went wrong/i),
    ).toBeInTheDocument();
    // The raw error text is NOT leaked as if it were a server rejection reason.
    expect(
      screen.queryByText(/internal server error/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('dice-roll')).not.toBeInTheDocument();
  });

  it('(reduced motion) renders the static server roll immediately, no animation', async () => {
    // prefers-reduced-motion: reduce — useReducedMotion reads matchMedia.
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );

    const client = new FakeGameClient({ betResult: diceBet });
    renderDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    const roll = await screen.findByTestId('dice-roll');
    // Static end-state is present and the stage marks itself static (no animation).
    expect(roll).toHaveTextContent('42.13');
    expect(screen.getByTestId('dice-stage')).toHaveAttribute(
      'data-static',
      'true',
    );
  });
});
