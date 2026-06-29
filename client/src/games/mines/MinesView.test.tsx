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
  BetRejectedError,
  type CrashSocketHandlers,
  type CrashSubscription,
  type FairnessResult,
  type GameClient,
  GameClientProvider,
  type GameStateProjection,
} from '../../lib/transport';
import MinesView from './MinesView';

// An OPEN Mines round as the bet loop returns it: status ACTIVE, outcome is the
// engine's `public_view` at k=0 — `{status, k:0, revealed:[], nextMultiplier}`
// with NO currentMultiplier yet (it appears only once k>=1). roundId === betId.
const OPEN_BET_ID = 'bet-mines-1';
const openBet: BetObject = {
  betId: OPEN_BET_ID,
  gameId: 'originals.mines',
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'ACTIVE',
  fairness: { serverSeedHash: 'hash-abc', clientSeed: 'cs-1', nonce: 7 },
  input: { mines: 3 },
  outcome: { status: 'ACTIVE', k: 0, revealed: [], nextMultiplier: 1.13 },
  createdAt: null,
  settledAt: null,
  idempotencyKeys: { debit: 'd1', credit: 'c1' },
};

// A scripted /action transport. Each test injects the projection(s) the server
// would return for the action(s) it performs; the fake just records the request
// and replays the next scripted projection (server-authoritative — the client
// renders these verbatim).
class FakeGameClient implements GameClient {
  readonly isDemo = false;
  betCalls: Array<{ gameId: string; input: BetRequest }> = [];
  actionCalls: Array<{ gameId: string; input: ActionRequest }> = [];
  meCalls = 0;

  constructor(
    private readonly opts: {
      betResult?: BetObject;
      betError?: Error;
      actions?: GameStateProjection[];
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
      balanceMinor: 1_000_000,
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

  async action(
    gameId: string,
    input: ActionRequest,
  ): Promise<GameStateProjection> {
    this.actionCalls.push({ gameId, input });
    const next = this.opts.actions?.[this.actionCalls.length - 1];
    return next ?? {};
  }

  async state(): Promise<GameStateProjection> {
    return {};
  }

  async fairness(betId: string): Promise<FairnessResult> {
    const disclosure: FairnessDisclosure = {
      betId,
      gameId: 'originals.mines',
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

function renderMines(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <MinesView />
      </SessionProvider>
    </GameClientProvider>,
  );
}

async function openRound(): Promise<void> {
  await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));
  await screen.findByLabelText('Cell 0');
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('MinesView — thin renderer of a server-held Mines round', () => {
  it('opens a round with the chosen mine count and renders the ACTIVE board', async () => {
    const client = new FakeGameClient();
    renderMines(client);
    await waitFor(() => expect(client.meCalls).toBe(1)); // mount bootstrap

    await openRound();

    expect(client.betCalls).toHaveLength(1);
    expect(client.betCalls[0].gameId).toBe('originals.mines');
    // The mines count is sent as bet INPUT — never an outcome (default 3).
    expect(client.betCalls[0].input.input).toEqual({ mines: 3 });
    // 25 clickable cells rendered.
    expect(screen.getByLabelText('Cell 24')).toBeInTheDocument();
    // Balance re-fetched after the open debit.
    await waitFor(() => expect(client.meCalls).toBe(2));
  });

  it('reveals a safe cell via action(roundId, reveal, cell) and advances k + multiplier', async () => {
    const safe: GameStateProjection = {
      cell: 6,
      safe: true,
      k: 1,
      currentMultiplier: 1.13,
      nextMultiplier: 1.29,
      status: 'ACTIVE',
      roundId: OPEN_BET_ID,
    };
    const client = new FakeGameClient({ actions: [safe] });
    renderMines(client);
    await openRound();

    await userEvent.click(screen.getByLabelText('Cell 6'));

    await waitFor(() => expect(client.actionCalls).toHaveLength(1));
    expect(client.actionCalls[0].gameId).toBe('originals.mines');
    expect(client.actionCalls[0].input).toEqual({
      roundId: OPEN_BET_ID,
      op: 'reveal',
      cell: 6,
    });
    // roundId threaded from the OPEN betId (server sets GameRound.id = betId).
    expect(client.actionCalls[0].input.roundId).toBe(openBet.betId);
    // Server multiplier rendered verbatim; k advanced.
    await waitFor(() =>
      expect(screen.getByTestId('mines-current')).toHaveTextContent('1.13×'),
    );
  });

  it('reveals a mine → LOST: discloses minePositions, no payout credited', async () => {
    const hit: GameStateProjection = {
      cell: 2,
      safe: false,
      k: 0,
      status: 'LOST',
      minePositions: [2, 7, 19],
      roundId: OPEN_BET_ID,
    };
    const client = new FakeGameClient({ actions: [hit] });
    renderMines(client);
    await openRound();
    await waitFor(() => expect(client.meCalls).toBe(2)); // post-open refresh

    await userEvent.click(screen.getByLabelText('Cell 2'));

    expect(await screen.findByText(/busted/i)).toBeInTheDocument();
    // All disclosed mines marked on the board.
    expect(screen.getByLabelText('Cell 7')).toHaveAttribute(
      'data-mine',
      'true',
    );
    expect(screen.getByLabelText('Cell 19')).toHaveAttribute(
      'data-mine',
      'true',
    );
    // No win framing / payout on a loss (the thin renderer grants no phantom credit).
    expect(screen.queryByTestId('mines-payout')).not.toBeInTheDocument();
    // Cashout is gone once the round is terminal.
    expect(
      screen.queryByRole('button', { name: /cash out/i }),
    ).not.toBeInTheDocument();
    // Terminal still re-fetches the server balance.
    await waitFor(() => expect(client.meCalls).toBe(3));
  });

  it('cashes out via action(roundId, cashout) and renders the server payout', async () => {
    const safe: GameStateProjection = {
      cell: 6,
      safe: true,
      k: 1,
      currentMultiplier: 1.13,
      nextMultiplier: 1.29,
      status: 'ACTIVE',
      roundId: OPEN_BET_ID,
    };
    const cashed: GameStateProjection = {
      status: 'CASHED_OUT',
      k: 1,
      multiplier: 1.13,
      minePositions: [2, 7, 19],
      roundId: OPEN_BET_ID,
      payoutMinor: 113,
    };
    const client = new FakeGameClient({ actions: [safe, cashed] });
    renderMines(client);
    await openRound();

    await userEvent.click(screen.getByLabelText('Cell 6'));
    await waitFor(() =>
      expect(screen.getByTestId('mines-current')).toHaveTextContent('1.13×'),
    );
    await userEvent.click(screen.getByRole('button', { name: /cash out/i }));

    await waitFor(() => expect(client.actionCalls).toHaveLength(2));
    expect(client.actionCalls[1].input).toEqual({
      roundId: OPEN_BET_ID,
      op: 'cashout',
    });
    // Server payoutMinor (113) formatted via formatMinor → "1.13".
    expect(await screen.findByTestId('mines-payout')).toHaveTextContent('1.13');
    // Balance re-fetched after the winning cashout (mount + open + cashout).
    await waitFor(() => expect(client.meCalls).toBe(3));
  });

  it('disables cash out until at least one safe reveal (k >= 1)', async () => {
    const client = new FakeGameClient();
    renderMines(client);
    await openRound();

    expect(screen.getByRole('button', { name: /cash out/i })).toBeDisabled();
  });

  it('surfaces a server bet-rejection reason and opens no round', async () => {
    const client = new FakeGameClient({
      betError: new BetRejectedError(403, 'bet blocked: stake exceeds max bet'),
    });
    renderMines(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/stake exceeds max bet/i),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Cell 0')).not.toBeInTheDocument();
  });

  it('surfaces a NEUTRAL message on a non-rejection transport fault', async () => {
    const client = new FakeGameClient({
      betError: new Error('Internal Server Error'),
    });
    renderMines(client);

    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(
      await screen.findByText(/something went wrong/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/internal server error/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Cell 0')).not.toBeInTheDocument();
  });
});
