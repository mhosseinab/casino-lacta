import { act, render, screen, waitFor } from '@testing-library/react';
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
  type CrashSocketHandlers,
  type CrashSubscription,
  type FairnessResult,
  type GameClient,
  GameClientProvider,
  type GameStateProjection,
} from '../../lib/transport';
import CrashView from './CrashView';

// A GameClient whose crashSocket CAPTURES its handlers so the test drives the
// server stream (round/tick/crash) deterministically, and whose state() returns a
// scripted resume projection. No real socket/timers/RNG.
class FakeGameClient implements GameClient {
  readonly isDemo = false;
  socketCalls = 0;
  closeCalls = 0;
  handlers: CrashSocketHandlers | null = null;

  constructor(private readonly opts: { resume?: GameStateProjection } = {}) {}

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
    return {
      userId: 'u1',
      walletId: 'w1',
      currency: 'GOLD',
      mode: 'PLAY',
      balanceMinor: 1_000_000,
    };
  }

  async bet(_g: string, _i: BetRequest): Promise<BetObject> {
    throw new Error('crash has no bet path');
  }
  async spin(_g: string, _i: BetRequest): Promise<BetObject> {
    throw new Error('crash has no spin path');
  }
  async action(): Promise<GameStateProjection> {
    return {};
  }

  async state(): Promise<GameStateProjection> {
    return this.opts.resume ?? { round: null, bets: [] };
  }

  async fairness(betId: string): Promise<FairnessResult> {
    const disclosure: FairnessDisclosure = {
      betId,
      gameId: 'originals.crash',
      serverSeedHash: 'hash',
      serverSeed: null,
      revealed: false,
      clientSeed: 'cs',
      nonce: 1,
      derivation: 'HMAC-SHA256',
      verifierUrl: `https://verify.example/${betId}`,
    };
    return { available: true, disclosure };
  }

  crashSocket(handlers: CrashSocketHandlers): CrashSubscription {
    this.socketCalls += 1;
    this.handlers = handlers;
    return {
      close: () => {
        this.closeCalls += 1;
      },
    };
  }
}

function renderCrash(client: GameClient) {
  return render(
    <GameClientProvider client={client}>
      <SessionProvider>
        <CrashView />
      </SessionProvider>
    </GameClientProvider>,
  );
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('CrashView — spectator of the server-decided shared round', () => {
  it('renders a full round→tick→crash sequence: phases + SERVER crash point + revealed seed', async () => {
    const client = new FakeGameClient();
    renderCrash(client);
    // Subscribed on mount.
    await waitFor(() => expect(client.socketCalls).toBe(1));
    const h = client.handlers;
    if (h === null) throw new Error('no handlers captured');

    act(() => h.onOpen?.());
    act(() =>
      h.onEvent({
        type: 'round',
        roundId: 'round-7',
        roundNumber: 7,
        serverSeedHash: 'hash-7',
        status: 'WAITING',
      }),
    );
    expect(screen.getByTestId('crash-phase')).toHaveTextContent(/starting/i);
    // Commitment shown, seed NOT revealed yet.
    expect(screen.getByTestId('crash-seed-hash')).toHaveTextContent('hash-7');
    expect(screen.queryByTestId('crash-seed-revealed')).not.toBeInTheDocument();

    act(() =>
      h.onEvent({
        type: 'tick',
        roundId: 'round-7',
        roundNumber: 7,
        multiplier: 1.42,
      }),
    );
    expect(screen.getByTestId('crash-phase')).toHaveTextContent(/in progress/i);
    // The multiplier reflects the SERVER tick verbatim.
    expect(screen.getByTestId('crash-multiplier')).toHaveTextContent('1.42×');

    act(() =>
      h.onEvent({
        type: 'crash',
        roundId: 'round-7',
        roundNumber: 7,
        crashPoint: 1.87,
        serverSeed: 'seed-raw-7',
        serverSeedHash: 'hash-7',
        status: 'CRASHED',
      }),
    );
    // CRASHED: the banner + multiplier show the SERVER crash point (not a client value).
    expect(screen.getByTestId('crash-phase')).toHaveTextContent('1.87×');
    expect(screen.getByTestId('crash-multiplier')).toHaveTextContent('1.87×');
    // The raw seed is revealed (it hashes back to the committed hash).
    expect(screen.getByTestId('crash-seed-revealed')).toHaveTextContent(
      'seed-raw-7',
    );
  });

  it('resumes the current shared round from /state on mount (RUNNING, no reveal)', async () => {
    const client = new FakeGameClient({
      resume: {
        round: {
          roundId: 'round-9',
          roundNumber: 9,
          serverSeedHash: 'hash-9',
          status: 'RUNNING',
          multiplier: 2.31,
        },
        bets: [],
      },
    });
    renderCrash(client);

    // The resume restores the live shared round verbatim.
    await waitFor(() =>
      expect(screen.getByTestId('crash-multiplier')).toHaveTextContent('2.31×'),
    );
    expect(screen.getByTestId('crash-phase')).toHaveTextContent(/in progress/i);
    expect(screen.getByTestId('crash-seed-hash')).toHaveTextContent('hash-9');
    // A live (non-settled) round withholds the raw seed.
    expect(screen.queryByTestId('crash-seed-revealed')).not.toBeInTheDocument();
  });

  it('reconnects with backoff after the socket closes (and never on unmount)', async () => {
    vi.useFakeTimers();
    const client = new FakeGameClient();
    const { unmount } = renderCrash(client);
    expect(client.socketCalls).toBe(1);

    // The stream drops → schedule a reconnect (backoff attempt 0 = 500ms).
    act(() => client.handlers?.onClose?.({ code: 1006, reason: 'drop' }));
    expect(screen.getByTestId('crash-connection')).toHaveAttribute(
      'data-connected',
      'false',
    );
    expect(client.socketCalls).toBe(1); // not yet — waiting out the backoff
    act(() => vi.advanceTimersByTime(500));
    expect(client.socketCalls).toBe(2); // reconnected

    // Unmount closes the subscription and does NOT schedule another reconnect.
    unmount();
    expect(client.closeCalls).toBeGreaterThanOrEqual(1);
    act(() => vi.advanceTimersByTime(60_000));
    expect(client.socketCalls).toBe(2);
  });

  it('coalesces a browser error→close into a SINGLE reconnect (no duplicate sockets)', () => {
    vi.useFakeTimers();
    const client = new FakeGameClient();
    renderCrash(client);
    expect(client.socketCalls).toBe(1);

    // A real errored socket fires onError THEN onClose (both wired in HttpGameClient);
    // one disconnect must yield exactly ONE reconnect, not two.
    act(() => {
      client.handlers?.onError?.(new Error('socket error'));
      client.handlers?.onClose?.({ code: 1006, reason: 'drop' });
    });
    act(() => vi.advanceTimersByTime(500));
    expect(client.socketCalls).toBe(2); // single reconnect, no duplicate socket

    // No leaked second timer fires a third connect later.
    act(() => vi.advanceTimersByTime(60_000));
    expect(client.socketCalls).toBe(2);
  });

  it('shows a spectator notice (betting/cash-out are not wired in this view)', async () => {
    const client = new FakeGameClient();
    renderCrash(client);
    expect(await screen.findByTestId('crash-spectator-note')).toHaveTextContent(
      /betting and cash-out open in a later release/i,
    );
    // No bet button exists — this is a pure spectator.
    expect(
      screen.queryByRole('button', { name: /bet|cash out/i }),
    ).not.toBeInTheDocument();
  });
});
