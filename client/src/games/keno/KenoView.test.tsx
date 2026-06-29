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
import KenoView from './KenoView';

// A real `originals.keno` settled BetObject — its `outcome` is the engine's
// Keno.play detail (drawn/hits/picks/risk) flattened with the bet-loop's server
// multiplier + payoutMinor (engine/games/keno.py + app/games/bet_loop.py).
// Rendered VERBATIM. Fixture is internally consistent:
//   picks [7,18,24,33,40] ∩ drawn → {7,18,24} → hits 3, so the cosmetic
//   intersection agrees with the server `hits`. multiplier 3.5 → payoutMinor 350.
const kenoBet: BetObject = {
  betId: 'bet-keno-1',
  gameId: 'originals.keno',
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'SETTLED',
  fairness: { serverSeedHash: 'hash-abc', clientSeed: 'cs-1', nonce: 7 },
  input: { picks: [7, 18, 24, 33, 40], risk: 'MEDIUM' },
  outcome: {
    drawn: [7, 18, 24, 1, 2, 3, 4, 5, 6, 8],
    hits: 3,
    picks: [7, 18, 24, 33, 40],
    risk: 'MEDIUM',
    multiplier: 3.5,
    payoutMinor: 350,
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
    return this.opts.betResult ?? kenoBet;
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
      gameId: 'originals.keno',
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

function renderKeno(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <KenoView />
      </SessionProvider>
    </GameClientProvider>,
  );
}

async function pick(...numbers: number[]): Promise<void> {
  for (const n of numbers) {
    await userEvent.click(screen.getByRole('button', { name: String(n) }));
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('KenoView — thin renderer of a server-decided keno bet', () => {
  it('sends the chosen picks + risk as bet input (never an outcome)', async () => {
    const client = new FakeGameClient({ betResult: kenoBet });
    renderKeno(client);

    await pick(24, 7, 18); // out of order → stored sorted
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    await waitFor(() => expect(client.betCalls).toHaveLength(1));
    expect(client.betCalls[0].gameId).toBe('originals.keno');
    expect(client.betCalls[0].input.input).toEqual({
      picks: [7, 18, 24],
      risk: 'MEDIUM',
    });
  });

  it('sends the chosen risk level when changed from the default', async () => {
    const client = new FakeGameClient({ betResult: kenoBet });
    renderKeno(client);

    await pick(7, 18, 24);
    await userEvent.click(screen.getByRole('button', { name: 'High' }));
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    await waitFor(() => expect(client.betCalls).toHaveLength(1));
    expect(client.betCalls[0].input.input).toEqual({
      picks: [7, 18, 24],
      risk: 'HIGH',
    });
  });

  it('renders the server multiplier, payout (formatted), hits and drawn numbers VERBATIM', async () => {
    const client = new FakeGameClient({ betResult: kenoBet });
    renderKeno(client);

    await pick(7, 18, 24, 33, 40);
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    // multiplier (3.5×) + payout (350 → "3.50") off outcome, scoped to the result
    // panel (BetControls "2×" chip would otherwise collide on a bare query).
    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText(/3\.5×/)).toBeInTheDocument();
    expect(result.getByText('3.50')).toBeInTheDocument();

    // hits count is the SERVER value, verbatim (not a client recomputation).
    expect(screen.getByTestId('keno-hits')).toHaveTextContent('3');

    // drawn numbers shown verbatim, scoped to the authoritative readout.
    const drawn = within(screen.getByTestId('keno-drawn'));
    expect(drawn.getByText('7')).toBeInTheDocument();
    expect(drawn.getByText('24')).toBeInTheDocument();
    expect(drawn.getByText('8')).toBeInTheDocument();
  });

  it('renders a LOSING result verbatim — 0× / 0.00 payout, no phantom credit', async () => {
    const losingBet: BetObject = {
      ...kenoBet,
      betId: 'bet-keno-loss',
      outcome: {
        drawn: [9, 10, 11, 12, 13, 14, 15, 16, 17, 19],
        hits: 0,
        picks: [7, 18, 24, 33, 40],
        risk: 'MEDIUM',
        multiplier: 0,
        payoutMinor: 0,
      },
    };
    const client = new FakeGameClient({ betResult: losingBet });
    renderKeno(client);

    await pick(7, 18, 24, 33, 40);
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText('0×')).toBeInTheDocument();
    expect(result.getByText('0.00')).toBeInTheDocument();
    expect(screen.getByTestId('keno-hits')).toHaveTextContent('0');
  });

  it('cannot bet with zero picks (no bet call, prompt to select)', async () => {
    const client = new FakeGameClient({ betResult: kenoBet });
    renderKeno(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(client.betCalls).toHaveLength(0);
    expect(
      await screen.findByText(/select at least 1 number/i),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Bet result')).not.toBeInTheDocument();
  });

  it('cannot select more than 10 numbers', async () => {
    const client = new FakeGameClient({ betResult: kenoBet });
    renderKeno(client);

    await pick(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11);

    // The 11th tap is a no-op: exactly 10 cells remain pressed and 11 is not.
    const pressed = screen
      .getAllByRole('button', { pressed: true })
      .filter((b) => /^\d+$/.test(b.textContent ?? ''));
    expect(pressed).toHaveLength(10);
    expect(screen.getByRole('button', { name: '11' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('re-fetches the server balance after a settle (never computes it)', async () => {
    const client = new FakeGameClient({ betResult: kenoBet });
    renderKeno(client);
    await waitFor(() => expect(client.meCalls).toBe(1));

    await pick(7, 18, 24);
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));
    await screen.findByLabelText('Bet result');

    await waitFor(() => expect(client.meCalls).toBe(2));
  });

  it('surfaces a server bet-rejection reason and renders no result', async () => {
    const client = new FakeGameClient({
      betError: new BetRejectedError(403, 'bet blocked: max win exceeded'),
    });
    renderKeno(client);

    await pick(7, 18, 24);
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(await screen.findByText(/max win exceeded/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('Bet result')).not.toBeInTheDocument();
  });

  it('surfaces a NEUTRAL message on a non-rejection transport fault', async () => {
    const client = new FakeGameClient({
      betError: new Error('Internal Server Error'),
    });
    renderKeno(client);

    await pick(7, 18, 24);
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/something went wrong/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/internal server error/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Bet result')).not.toBeInTheDocument();
  });

  it('(reduced motion) reveals the drawn numbers immediately', async () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: true,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );

    const client = new FakeGameClient({ betResult: kenoBet });
    renderKeno(client);

    await pick(7, 18, 24, 33, 40);
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    await screen.findByLabelText('Bet result');
    expect(screen.getByTestId('keno-grid')).toHaveAttribute(
      'data-static',
      'true',
    );
  });
});
