import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
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
import PocketDiceView from './PocketDiceView';

// A real `originals.pocketdice` settled BetObject — its `outcome` is the engine's
// PocketDice detail (dice/sum/target/direction/won) flattened with the bet-loop's
// server-stamped `multiplier` + `payoutMinor` (engine/games/pocketdice.py +
// app/games/bet_loop.py). Rendered VERBATIM — the client computes none of it.
// dice [2,3] sum 5 < target 7 (UNDER) → won. multiplier (1-edge)/p = 2.376 → the
// server floors the payout: 100 × 2.376 = 237.6 → payoutMinor 237, multiplier 2.37.
const pocketBet: BetObject = {
  betId: 'bet-pocket-1',
  gameId: 'originals.pocketdice',
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'SETTLED',
  fairness: { serverSeedHash: 'hash-abc', clientSeed: 'cs-1', nonce: 7 },
  input: { target: 7, direction: 'UNDER' },
  outcome: {
    dice: [2, 3],
    sum: 5,
    target: 7,
    direction: 'UNDER',
    won: true,
    multiplier: 2.37,
    payoutMinor: 237,
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
    return this.opts.betResult ?? pocketBet;
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
      gameId: 'originals.pocketdice',
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

function renderPocketDice(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <PocketDiceView />
      </SessionProvider>
    </GameClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('PocketDiceView — thin renderer of a server-decided pocket-dice bet', () => {
  it('sends the chosen target + direction as bet input (never an outcome)', async () => {
    const client = new FakeGameClient({ betResult: pocketBet });
    renderPocketDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    await waitFor(() => expect(client.betCalls).toHaveLength(1));
    expect(client.betCalls[0].gameId).toBe('originals.pocketdice');
    expect(client.betCalls[0].input.input).toEqual({
      target: 7,
      direction: 'UNDER',
    });
  });

  it('makes the forbidden zero-win bets unselectable: UNDER 2 snaps to 3, and OVER 12 snaps to 11', async () => {
    const client = new FakeGameClient({ betResult: pocketBet });
    renderPocketDice(client);
    const targetInput = screen.getByLabelText('Target sum') as HTMLInputElement;

    // UNDER 2 can never win (no 2d6 sum < 2) → snaps to the lowest valid UNDER target.
    fireEvent.change(targetInput, { target: { value: '2' } });
    expect(targetInput.value).toBe('3');

    // 12 is valid under UNDER; toggling to OVER makes OVER 12 forbidden → snaps to 11.
    fireEvent.change(targetInput, { target: { value: '12' } });
    expect(targetInput.value).toBe('12');
    await userEvent.click(
      screen.getByRole('button', { name: /switch over\/under/i }),
    );
    expect(targetInput.value).toBe('11');

    // The bet then sends the clamped, non-forbidden input — never a zero-win target.
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));
    await waitFor(() => expect(client.betCalls).toHaveLength(1));
    expect(client.betCalls[0].input.input).toEqual({
      target: 11,
      direction: 'OVER',
    });
  });

  it('renders the server multiplier, payout (formatted) and dice + sum VERBATIM', async () => {
    const client = new FakeGameClient({ betResult: pocketBet });
    renderPocketDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    // multiplier (2.37×) + payout (237 → "2.37") come straight off outcome. Scope
    // to the result panel — "2×" is also a BetControls quick-stake chip.
    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText(/2\.37×/)).toBeInTheDocument();
    expect(result.getByText('2.37')).toBeInTheDocument();

    // The cosmetic faces + authoritative sum, read verbatim off outcome.
    const faces = within(screen.getByTestId('pocketdice-result'));
    expect(faces.getByText('5')).toBeInTheDocument();
    expect(screen.getByLabelText('Die 1 showing 2')).toBeInTheDocument();
    expect(screen.getByLabelText('Die 2 showing 3')).toBeInTheDocument();
  });

  it('renders a LOSING result verbatim — 0× / 0.00 payout, dice shown, no win credit', async () => {
    // A real losing settled bet: dice [6,6] sum 12, not < target 7 → won:false,
    // multiplier 0, payoutMinor 0. The thin renderer shows the loss exactly and
    // never grants a phantom credit. This is the trust-boundary branch.
    const losingBet: BetObject = {
      ...pocketBet,
      betId: 'bet-pocket-loss',
      outcome: {
        dice: [6, 6],
        sum: 12,
        target: 7,
        direction: 'UNDER',
        won: false,
        multiplier: 0,
        payoutMinor: 0,
      },
    };
    const client = new FakeGameClient({ betResult: losingBet });
    renderPocketDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    const result = within(await screen.findByLabelText('Bet result'));
    expect(result.getByText('0×')).toBeInTheDocument();
    expect(result.getByText('0.00')).toBeInTheDocument();

    // Sum shown verbatim; loss framing ("Lost"), never "Won".
    const faces = screen.getByTestId('pocketdice-result');
    expect(faces).toHaveTextContent('12');
    expect(faces).toHaveTextContent(/lost/i);
    expect(faces).not.toHaveTextContent(/won/i);
  });

  it('shows the preview win chance for the target (41.67% at UNDER 7)', () => {
    const client = new FakeGameClient();
    renderPocketDice(client);
    // p = 15/36 → 41.6667% → "41.67 %" (edge-free, fair 2d6).
    expect(screen.getByText('41.67 %')).toBeInTheDocument();
  });

  it('re-fetches the server balance after a settle (never computes it)', async () => {
    const client = new FakeGameClient({ betResult: pocketBet });
    renderPocketDice(client);
    await waitFor(() => expect(client.meCalls).toBe(1));

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));
    await screen.findByTestId('pocketdice-result');

    await waitFor(() => expect(client.meCalls).toBe(2));
  });

  it('surfaces a server bet-rejection reason and renders no result', async () => {
    const client = new FakeGameClient({
      betError: new BetRejectedError(403, 'bet blocked: stake exceeds max'),
    });
    renderPocketDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(await screen.findByText(/stake exceeds max/i)).toBeInTheDocument();
    expect(screen.queryByTestId('pocketdice-result')).not.toBeInTheDocument();
  });

  it('surfaces a NEUTRAL message on a non-rejection transport fault', async () => {
    const client = new FakeGameClient({
      betError: new Error('Internal Server Error'),
    });
    renderPocketDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/something went wrong/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/internal server error/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('pocketdice-result')).not.toBeInTheDocument();
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

    const client = new FakeGameClient({ betResult: pocketBet });
    renderPocketDice(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    const faces = await screen.findByTestId('pocketdice-result');
    expect(faces).toHaveTextContent('5');
    expect(screen.getByTestId('pocketdice-faces')).toHaveAttribute(
      'data-static',
      'true',
    );
  });
});
