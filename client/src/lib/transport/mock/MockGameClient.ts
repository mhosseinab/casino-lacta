import type {
  ActionRequest,
  BetObject,
  BetRequest,
  GuestSessionResponse,
  MeResponse,
} from '../../../contracts';
import type {
  ActionResult,
  CrashEvent,
  CrashSocketHandlers,
  CrashSubscription,
  FairnessResult,
  GameClient,
  GameStateProjection,
} from '../GameClient';

// =========================================================================== //
// QUARANTINE: this is the ONE file in the whole client permitted to use
// `Math.random`. It is a DEMO transport — with no server there is nothing to
// decide outcomes, so it FABRICATES plausible, contract-shaped results purely so
// the static Pages build is playable. It is NOT a renderer escape hatch: views
// still depend only on GameClient and decide nothing. The iron rule ("the server
// decides every outcome, and proves it") is preserved by isolating the make-believe
// here AND by refusing to fake a provably-fair proof (see fairness()).
// =========================================================================== //
export class MockGameClient implements GameClient {
  readonly isDemo = true;

  // A cosmetic running balance so /me feels alive across demo bets.
  private balanceMinor = 1_000_000;

  async startGuestSession(): Promise<GuestSessionResponse> {
    return {
      userId: this.demoId('user'),
      walletId: this.demoId('wallet'),
      currency: 'GOLD',
      mode: 'PLAY',
      tokens: {
        accessToken: this.demoId('access'),
        refreshToken: this.demoId('refresh'),
        tokenType: 'bearer',
      },
    };
  }

  async me(): Promise<MeResponse> {
    return {
      userId: 'demo-user',
      walletId: 'demo-wallet',
      currency: 'GOLD',
      mode: 'PLAY',
      balanceMinor: this.balanceMinor,
    };
  }

  async bet(gameId: string, input: BetRequest): Promise<BetObject> {
    return this.settle(gameId, input);
  }

  async spin(gameId: string, input: BetRequest): Promise<BetObject> {
    return this.settle(gameId, input);
  }

  async action(
    gameId: string,
    actionInput: ActionRequest,
  ): Promise<ActionResult> {
    return {
      demo: true,
      gameId,
      roundId: actionInput.roundId,
      op: actionInput.op,
      status: 'ACTIVE',
    };
  }

  async state(gameId: string): Promise<GameStateProjection> {
    return { demo: true, gameId, status: 'NONE' };
  }

  // Cannot produce a real commit-reveal proof without a server — say so, never fake it.
  async fairness(_betId: string): Promise<FairnessResult> {
    return {
      available: false,
      reason:
        'Fairness proof is unavailable in demo mode — outcomes are mocked, not server-verified.',
    };
  }

  crashSocket(handlers: CrashSocketHandlers): CrashSubscription {
    const roundId = this.demoId('round');
    const roundNumber = Math.floor(Math.random() * 1000);
    const crashPoint = Math.round((1 + Math.random() * 5) * 100) / 100;
    let multiplier = 1.0;
    let interval: ReturnType<typeof setInterval> | undefined;

    const start = setTimeout(() => {
      handlers.onOpen?.();
      const round: CrashEvent = {
        type: 'round',
        roundId,
        roundNumber,
        serverSeedHash: 'demo-unverified',
        status: 'WAITING',
      };
      handlers.onEvent(round);
      interval = setInterval(() => {
        multiplier = Math.round(multiplier * 1.06 * 100) / 100;
        if (multiplier >= crashPoint) {
          if (interval) clearInterval(interval);
          handlers.onEvent({
            type: 'crash',
            roundId,
            roundNumber,
            crashPoint,
            serverSeed: 'demo-unverified',
            serverSeedHash: 'demo-unverified',
            status: 'CRASHED',
          });
        } else {
          handlers.onEvent({ type: 'tick', roundId, roundNumber, multiplier });
        }
      }, 100);
    }, 0);

    return {
      close: () => {
        clearTimeout(start);
        if (interval) clearInterval(interval);
        handlers.onClose?.({ code: 1000, reason: 'demo closed' });
      },
    };
  }

  // --- fabrication helpers (the make-believe, all confined here) ---------------- //

  private settle(gameId: string, input: BetRequest): BetObject {
    const won = Math.random() < 0.49;
    const multiplier = won ? 1 + Math.random() * 2 : 0;
    const payoutMinor = Math.floor(input.stakeMinor * multiplier);
    this.balanceMinor += payoutMinor - input.stakeMinor;
    const now = new Date().toISOString();
    return {
      betId: input.betId,
      gameId,
      userId: 'demo-user',
      walletId: 'demo-wallet',
      currency: input.currency,
      mode: input.mode,
      stakeMinor: input.stakeMinor,
      status: 'SETTLED',
      fairness: {
        serverSeedHash: 'demo-unverified',
        clientSeed: this.demoId('seed'),
        nonce: Math.floor(Math.random() * 1_000_000),
      },
      input: input.input ?? {},
      outcome: { demo: true, won, multiplier, payoutMinor },
      createdAt: now,
      settledAt: now,
      idempotencyKeys: { settle: `demo-${input.betId}` },
    };
  }

  private demoId(prefix: string): string {
    return `demo-${prefix}-${Math.floor(Math.random() * 1e9).toString(36)}`;
  }
}
