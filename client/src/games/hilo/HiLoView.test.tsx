import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  ActionRequest,
  BetObject,
  BetRequest,
  FairnessDisclosure,
  GuestSessionResponse,
  MeResponse,
} from '../../contracts';
import { SessionProvider } from '../../lib/session';
import {
  type ActionResult,
  BetRejectedError,
  type CrashSocketHandlers,
  type CrashSubscription,
  type FairnessResult,
  type GameClient,
  GameClientProvider,
  type GameStateProjection,
} from '../../lib/transport';
import HiLoView from './HiLoView';

// The OPEN BetObject — its `outcome` is HiLo's public_view snapshot (hilo.py
// public_view), NOT a settle: ACTIVE, a shown card to guess on, 1.0× start, 0
// steps. `betId` is the roundId threaded into every /action (bet_loop.py).
const openBet: BetObject = {
  betId: 'round-hilo-1',
  gameId: 'originals.hilo',
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'ACTIVE',
  fairness: { serverSeedHash: 'hash-abc', clientSeed: 'cs-1', nonce: 7 },
  input: {},
  outcome: {
    status: 'ACTIVE',
    shownRank: 7, // a "7"
    currentMultiplier: 1.0,
    steps: 0,
  },
  createdAt: null,
  settledAt: null,
  idempotencyKeys: { open: 'k1' },
};

// A winning guess projection (Outcome.detail + roundId, bet_loop step_action):
// the revealed card becomes the new shown card; multiplier compounds; steps→1.
const winGuess: ActionResult = {
  status: 'ACTIVE',
  side: 'HIGHER',
  guessedFromRank: 7,
  revealedRank: 11, // a "J"
  shownRank: 11,
  won: true,
  stepMultiplier: 1.857,
  currentMultiplier: 1.857,
  steps: 1,
  roundId: 'round-hilo-1',
};

// A losing guess projection — NO shownRank, won:false, no credit.
const lossGuess: ActionResult = {
  status: 'LOST',
  side: 'HIGHER',
  guessedFromRank: 7,
  revealedRank: 3, // a "3" — lower, so HIGHER loses
  won: false,
  steps: 1,
  roundId: 'round-hilo-1',
};

// A cashout projection — server-stamped payoutMinor (185 → "1.85").
const cashout: ActionResult = {
  status: 'CASHED_OUT',
  multiplier: 1.857,
  currentMultiplier: 1.857,
  steps: 1,
  roundId: 'round-hilo-1',
  payoutMinor: 185,
};

class FakeGameClient implements GameClient {
  readonly isDemo = false;
  betCalls: Array<{ gameId: string; input: BetRequest }> = [];
  actionCalls: Array<{ gameId: string; input: ActionRequest }> = [];
  meCalls = 0;
  private balanceMinor = 1_000_000;
  private readonly scriptedActions: ActionResult[];

  constructor(
    private readonly opts: {
      betResult?: BetObject;
      betError?: Error;
      actions?: ActionResult[];
    } = {},
  ) {
    this.scriptedActions = [...(opts.actions ?? [])];
  }

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
    return this.opts.betResult ?? openBet;
  }

  async spin(gameId: string, input: BetRequest): Promise<BetObject> {
    return this.bet(gameId, input);
  }

  async action(gameId: string, input: ActionRequest): Promise<ActionResult> {
    this.actionCalls.push({ gameId, input });
    const next = this.scriptedActions.shift();
    if (next === undefined) throw new Error('no scripted action');
    return next;
  }

  async state(): Promise<GameStateProjection> {
    return {};
  }

  async fairness(betId: string): Promise<FairnessResult> {
    const disclosure: FairnessDisclosure = {
      betId,
      gameId: 'originals.hilo',
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

function renderHiLo(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <HiLoView />
      </SessionProvider>
    </GameClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const betButton = () => screen.getByRole('button', { name: /^bet$/i });
const higherButton = () => screen.getByRole('button', { name: /higher/i });
const lowerButton = () => screen.getByRole('button', { name: /lower/i });
const cashoutButton = () => screen.getByRole('button', { name: /cash out/i });

describe('HiLoView — thin renderer of a server-decided HiLo round', () => {
  it('opens with empty input {} and renders the ACTIVE shown card + 1.00× start; balance refetched', async () => {
    const client = new FakeGameClient({ betResult: openBet });
    renderHiLo(client);
    await waitFor(() => expect(client.meCalls).toBe(1)); // bootstrap /me

    await userEvent.click(betButton());

    await waitFor(() => expect(client.betCalls).toHaveLength(1));
    expect(client.betCalls[0].gameId).toBe('originals.hilo');
    expect(client.betCalls[0].input.input).toEqual({});

    // The open public_view shown card (a 7) + the 1.00× start, rendered verbatim.
    expect(await screen.findByTestId('hilo-card')).toHaveTextContent('7');
    expect(screen.getByTestId('hilo-multiplier')).toHaveTextContent('1.00');
    // Debit happens at open → balance re-fetched (never computed).
    await waitFor(() => expect(client.meCalls).toBe(2));
  });

  it('a winning guess sends {roundId:<open betId>, op:"guess", side} and advances the card + multiplier', async () => {
    const client = new FakeGameClient({
      betResult: openBet,
      actions: [winGuess],
    });
    renderHiLo(client);
    await userEvent.click(betButton());
    await screen.findByTestId('hilo-card');

    await userEvent.click(higherButton());

    await waitFor(() => expect(client.actionCalls).toHaveLength(1));
    expect(client.actionCalls[0].gameId).toBe('originals.hilo');
    expect(client.actionCalls[0].input).toEqual({
      roundId: 'round-hilo-1', // the OPEN betId
      op: 'guess',
      side: 'HIGHER',
    });
    // The revealed card (J) becomes the shown card; multiplier compounds to 1.86×.
    await waitFor(() =>
      expect(screen.getByTestId('hilo-card')).toHaveTextContent('J'),
    );
    expect(screen.getByTestId('hilo-multiplier')).toHaveTextContent('1.86');
    expect(screen.getByTestId('hilo-steps')).toHaveTextContent('1');
  });

  it('a losing guess → LOST: reveals the losing card, no credit, guess + cashout disabled', async () => {
    const client = new FakeGameClient({
      betResult: openBet,
      actions: [lossGuess],
    });
    renderHiLo(client);
    await userEvent.click(betButton());
    await screen.findByTestId('hilo-card');
    await waitFor(() => expect(client.meCalls).toBe(2)); // open refetch

    await userEvent.click(higherButton());

    // Busted, with the losing revealed card (a 3) shown.
    expect(await screen.findByTestId('hilo-busted')).toBeInTheDocument();
    expect(screen.getByTestId('hilo-card')).toHaveTextContent('3');
    // A loss credits nothing — but the balance is still re-fetched (terminal).
    await waitFor(() => expect(client.meCalls).toBe(3));
    // No further play.
    expect(higherButton()).toBeDisabled();
    expect(lowerButton()).toBeDisabled();
    expect(cashoutButton()).toBeDisabled();
  });

  it('cashout (after ≥1 win) sends {roundId, op:"cashout"}, renders CASHED_OUT + payout, refetches balance', async () => {
    const client = new FakeGameClient({
      betResult: openBet,
      actions: [winGuess, cashout],
    });
    renderHiLo(client);
    await userEvent.click(betButton());
    await screen.findByTestId('hilo-card');

    await userEvent.click(higherButton());
    await waitFor(() =>
      expect(screen.getByTestId('hilo-card')).toHaveTextContent('J'),
    );
    await waitFor(() => expect(client.meCalls).toBe(2)); // only open refetch so far

    await userEvent.click(cashoutButton());

    await waitFor(() => expect(client.actionCalls).toHaveLength(2));
    expect(client.actionCalls[1].input).toEqual({
      roundId: 'round-hilo-1',
      op: 'cashout',
    });
    // payoutMinor 185 formatted via formatMinor → "1.85".
    expect(await screen.findByTestId('hilo-cashed-out')).toHaveTextContent(
      '1.85',
    );
    // Cashout credits → balance re-fetched.
    await waitFor(() => expect(client.meCalls).toBe(3));
  });

  it('cashout is disabled until at least one successful guess (steps ≥ 1)', async () => {
    const client = new FakeGameClient({ betResult: openBet });
    renderHiLo(client);
    await userEvent.click(betButton());
    await screen.findByTestId('hilo-card');

    // Freshly opened: 0 steps → cashout present but disabled.
    expect(cashoutButton()).toBeDisabled();
  });

  it('surfaces a server bet-rejection on open and opens no round', async () => {
    const client = new FakeGameClient({
      betError: new BetRejectedError(403, 'bet blocked: insufficient funds'),
    });
    renderHiLo(client);
    await waitFor(() => expect(client.meCalls).toBe(1));

    await userEvent.click(betButton());

    expect(await screen.findByText(/insufficient funds/i)).toBeInTheDocument();
    expect(screen.queryByTestId('hilo-card')).not.toBeInTheDocument();
  });

  it('surfaces a NEUTRAL message when a cashout transport fault occurs, and keeps the round retryable', async () => {
    // Win one guess (cashout becomes enabled), then the cashout action throws
    // (no scripted action left) — a failed cashout in a money game must NOT fail
    // silently, and the round stays ACTIVE server-side so the player can retry.
    const client = new FakeGameClient({
      betResult: openBet,
      actions: [winGuess],
    });
    renderHiLo(client);
    await userEvent.click(betButton());
    await screen.findByTestId('hilo-card');
    await userEvent.click(higherButton());
    await waitFor(() =>
      expect(screen.getByTestId('hilo-card')).toHaveTextContent('J'),
    );

    await userEvent.click(cashoutButton());

    expect(await screen.findByTestId('hilo-error')).toHaveTextContent(
      /something went wrong/i,
    );
    // Not silently lost: no cashed-out/busted state, and cashout is retryable.
    expect(screen.queryByTestId('hilo-cashed-out')).not.toBeInTheDocument();
    expect(screen.queryByTestId('hilo-busted')).not.toBeInTheDocument();
    expect(cashoutButton()).not.toBeDisabled();
  });

  it('surfaces a NEUTRAL message on a non-rejection transport fault at open', async () => {
    const client = new FakeGameClient({
      betError: new Error('Internal Server Error'),
    });
    renderHiLo(client);
    await waitFor(() => expect(client.meCalls).toBe(1));

    await userEvent.click(betButton());

    expect(
      await screen.findByText(/something went wrong/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/internal server error/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('hilo-card')).not.toBeInTheDocument();
  });
});
