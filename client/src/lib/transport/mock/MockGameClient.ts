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
// A demo-only per-round store for the STATEFUL games (Mines/HiLo). With no server
// there is no held state, so the quarantine fabricates a round keyed by its open
// betId (== roundId) and advances it across /action calls. Make-believe only —
// confined here exactly like the instant fakes.
type MinesRound = {
  kind: 'mines';
  mines: number;
  minePositions: number[];
  revealed: number[];
  stakeMinor: number;
  edge: number;
};
type HiloRound = {
  kind: 'hilo';
  shownRank: number;
  cumulative: number;
  steps: number;
  stakeMinor: number;
  edge: number;
};
type DemoRound = MinesRound | HiloRound;

export class MockGameClient implements GameClient {
  readonly isDemo = true;

  // A cosmetic running balance so /me feels alive across demo bets.
  private balanceMinor = 1_000_000;

  // Open stateful rounds keyed by roundId (== the open betId).
  private readonly rounds = new Map<string, DemoRound>();
  // The caller's most recent round per game (for the /state resume stub).
  private lastRound: { gameId: string; roundId: string } | null = null;

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
    // Stateful games OPEN a round here (status ACTIVE, no settle); instant games
    // settle atomically.
    if (gameId === 'originals.mines') return this.openMines(input);
    if (gameId === 'originals.hilo') return this.openHilo(input);
    return this.settle(gameId, input);
  }

  async spin(gameId: string, input: BetRequest): Promise<BetObject> {
    return this.settle(gameId, input);
  }

  async action(
    gameId: string,
    actionInput: ActionRequest,
  ): Promise<ActionResult> {
    const round = this.rounds.get(actionInput.roundId);
    if (round?.kind === 'mines') return this.stepMines(round, actionInput);
    if (round?.kind === 'hilo') return this.stepHilo(round, actionInput);
    // Unknown/closed round — a harmless no-op projection.
    return {
      demo: true,
      gameId,
      roundId: actionInput.roundId,
      op: actionInput.op,
      status: 'ACTIVE',
    };
  }

  async state(gameId: string): Promise<GameStateProjection> {
    if (this.lastRound?.gameId === gameId) {
      const round = this.rounds.get(this.lastRound.roundId);
      const roundId = this.lastRound.roundId;
      if (round?.kind === 'mines') {
        const k = round.revealed.length;
        return {
          status: 'ACTIVE',
          k,
          revealed: round.revealed,
          ...(k >= 1
            ? { currentMultiplier: minesMultiplier(round.mines, k, round.edge) }
            : {}),
          ...(k < 25 - round.mines
            ? {
                nextMultiplier: minesMultiplier(round.mines, k + 1, round.edge),
              }
            : {}),
          roundId,
        };
      }
      if (round?.kind === 'hilo') {
        return {
          status: 'ACTIVE',
          shownRank: round.shownRank,
          currentMultiplier: round.cumulative,
          steps: round.steps,
          roundId,
        };
      }
    }
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
    const outcome = this.fabricate(gameId, input);
    this.balanceMinor += (outcome.payoutMinor as number) - input.stakeMinor;
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
      outcome,
      createdAt: now,
      settledAt: now,
      idempotencyKeys: { settle: `demo-${input.betId}` },
    };
  }

  // Fabricate a contract-shaped DICE outcome (spec §A.1) so demo mode actually
  // demonstrates the mechanic: a roll in [0,100), win per target/direction, and the
  // matching (1−edge)/p multiplier. Still make-believe (no server seeds) — the real
  // roll/payout always come from the server; this is confined to the demo quarantine.
  private fakeDice(input: BetRequest): Record<string, unknown> {
    const target = Number((input.input as { target?: number })?.target ?? 50);
    const direction =
      (input.input as { direction?: string })?.direction === 'UNDER'
        ? 'UNDER'
        : 'OVER';
    const roll = Math.round(Math.random() * 10_000) / 100; // [0.00, 100.00)
    const won = direction === 'OVER' ? roll > target : roll < target;
    const winPct = direction === 'OVER' ? 100 - target : target;
    const multiplier = won && winPct > 0 ? (1 - 0.01) / (winPct / 100) : 0;
    const payoutMinor = Math.floor(input.stakeMinor * multiplier);
    return {
      demo: true,
      roll,
      target,
      direction,
      won,
      multiplier,
      payoutMinor,
    };
  }

  // Fabricate a contract-shaped LIMBO outcome (spec §A.3) so demo mode shows the
  // mechanic: a generated multiplier X from the shared crash curve, win when
  // X ≥ target (pays `target`), else 0. Still make-believe (no server seeds) — the
  // real generated value/payout always come from the server. Demo quarantine only.
  private fakeLimbo(input: BetRequest): Record<string, unknown> {
    const target = Number((input.input as { target?: number })?.target ?? 2);
    // X = max(1.00, floor((1−edge)/(1−f) * 100)/100), f ∈ [0,1) — same curve as Crash.
    const f = Math.random();
    const generated = Math.max(1, Math.floor((0.99 / (1 - f)) * 100) / 100);
    const won = generated >= target;
    const multiplier = won ? target : 0;
    const payoutMinor = Math.floor(input.stakeMinor * multiplier);
    return { demo: true, generated, target, won, multiplier, payoutMinor };
  }

  // Dispatch an instant game to its fabricator (the make-believe outcome). Each
  // mirrors the engine's outcome KEYS so the view renders it verbatim; the numbers
  // are demo fakes (no server seeds), confined to this quarantine.
  private fabricate(
    gameId: string,
    input: BetRequest,
  ): Record<string, unknown> {
    switch (gameId) {
      case 'originals.dice':
        return this.fakeDice(input);
      case 'originals.limbo':
        return this.fakeLimbo(input);
      case 'originals.pocketdice':
        return this.fakePocketDice(input);
      case 'originals.keno':
        return this.fakeKeno(input);
      case 'originals.roulette':
        return this.fakeRoulette(input);
      case 'originals.plinko':
        return this.fakePlinko(input);
      default:
        return this.fakeGeneric(input);
    }
  }

  // Pocket Dice (spec §A.2): two d6 → sum 2..12; win UNDER/OVER a target; the win
  // pays (1−edge)/p from the triangular pmf. Make-believe dice; the real roll/payout
  // come from the server.
  private fakePocketDice(input: BetRequest): Record<string, unknown> {
    const target = Number((input.input as { target?: number })?.target ?? 7);
    const direction =
      (input.input as { direction?: string })?.direction === 'OVER'
        ? 'OVER'
        : 'UNDER';
    const d1 = Math.floor(Math.random() * 6) + 1;
    const d2 = Math.floor(Math.random() * 6) + 1;
    const sum = d1 + d2;
    const won = direction === 'OVER' ? sum > target : sum < target;
    const counts: Record<number, number> = {
      2: 1,
      3: 2,
      4: 3,
      5: 4,
      6: 5,
      7: 6,
      8: 5,
      9: 4,
      10: 3,
      11: 2,
      12: 1,
    };
    let region = 0;
    for (let s = 2; s <= 12; s++) {
      if (direction === 'OVER' ? s > target : s < target) region += counts[s];
    }
    const p = region / 36;
    const multiplier =
      won && p > 0 ? Math.round(((1 - 0.01) / p) * 100) / 100 : 0;
    const payoutMinor = Math.floor(input.stakeMinor * multiplier);
    return {
      demo: true,
      dice: [d1, d2],
      sum,
      target,
      direction,
      won,
      multiplier,
      payoutMinor,
    };
  }

  // Keno (spec §A.8): draw 10 distinct of 1..40; hits = picks ∩ drawn. The REAL
  // multiplier is the server's tuned per-(picks,risk) hypergeometric table — NOT
  // replicated here; the demo fabricates a plausible hits-scaled multiplier only.
  private fakeKeno(input: BetRequest): Record<string, unknown> {
    const picks = Array.isArray((input.input as { picks?: number[] })?.picks)
      ? (input.input as { picks: number[] }).picks
      : [];
    const risk = String((input.input as { risk?: string })?.risk ?? 'MEDIUM');
    const pool = Array.from({ length: 40 }, (_, i) => i + 1);
    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    const drawn = pool.slice(0, 10);
    const hits = picks.filter((n) => drawn.includes(n)).length;
    const k = Math.max(1, picks.length);
    const riskScale = risk === 'HIGH' ? 3 : risk === 'LOW' ? 1 : 1.8;
    const multiplier =
      hits === 0 ? 0 : Math.round((hits / k) * hits * riskScale * 100) / 100;
    const payoutMinor = Math.floor(input.stakeMinor * multiplier);
    return { demo: true, drawn, hits, picks, risk, multiplier, payoutMinor };
  }

  // Roulette 0–99 (spec §A.9): one draw → result 0..99 → colour (GREEN[0] / RED[1..49]
  // / BLACK[50..99]); each colour bet pays (1−edge)/p, aggregated stake-weighted.
  private fakeRoulette(input: BetRequest): Record<string, unknown> {
    const bets = Array.isArray(
      (input.input as { bets?: Array<{ value: string; stakeMinor: number }> })
        ?.bets,
    )
      ? (input.input as { bets: Array<{ value: string; stakeMinor: number }> })
          .bets
      : [];
    const result = Math.floor(Math.random() * 100);
    const colour = result === 0 ? 'GREEN' : result < 50 ? 'RED' : 'BLACK';
    const pockets: Record<string, number> = { GREEN: 1, RED: 49, BLACK: 50 };
    let weighted = 0;
    let total = 0;
    const settlements = bets.map((b, i) => {
      const won = b.value === colour;
      const p = (pockets[b.value] ?? 1) / 100;
      const m = won ? (1 - 0.01) / p : 0;
      weighted += b.stakeMinor * m;
      total += b.stakeMinor;
      return { bet: i, value: b.value, won, multiplier: m };
    });
    const aggregate = total > 0 ? weighted / total : 0;
    const payoutMinor = Math.floor(total * aggregate);
    return {
      demo: true,
      result,
      colour,
      settlements,
      multiplier: aggregate,
      payoutMinor,
    };
  }

  // Plinko (spec §A.5): bounce through `rows` rows → bin = rightBounces. The REAL
  // multiplier is the server's tuned per-(rows,risk) table — NOT replicated here; the
  // demo fabricates a convex (edges-high) multiplier and keeps path↔bin consistent.
  private fakePlinko(input: BetRequest): Record<string, unknown> {
    const rows = Number((input.input as { rows?: number })?.rows ?? 12);
    const risk = String((input.input as { risk?: string })?.risk ?? 'MEDIUM');
    const path: string[] = [];
    let right = 0;
    for (let i = 0; i < rows; i++) {
      if (Math.random() < 0.5) {
        right += 1;
        path.push('R');
      } else {
        path.push('L');
      }
    }
    const bin = right;
    const centre = rows / 2;
    const norm = centre === 0 ? 0 : Math.abs(bin - centre) / centre; // 0..1
    const peak = risk === 'HIGH' ? 9 : risk === 'LOW' ? 1.4 : 3;
    const multiplier = Math.round((0.5 + norm * norm * peak) * 100) / 100;
    const payoutMinor = Math.floor(input.stakeMinor * multiplier);
    return {
      demo: true,
      rows,
      risk,
      bin,
      rightBounces: right,
      path,
      multiplier,
      payoutMinor,
    };
  }

  // --- stateful demo rounds (Mines/HiLo): open + step the fabricated round -------- //

  private openMines(input: BetRequest): BetObject {
    const mines = Number((input.input as { mines?: number })?.mines ?? 3);
    const positions = new Set<number>();
    while (positions.size < mines)
      positions.add(Math.floor(Math.random() * 25));
    const minePositions = [...positions].sort((a, b) => a - b);
    this.rounds.set(input.betId, {
      kind: 'mines',
      mines,
      minePositions,
      revealed: [],
      stakeMinor: input.stakeMinor,
      edge: 0.01,
    });
    this.lastRound = { gameId: 'originals.mines', roundId: input.betId };
    this.balanceMinor -= input.stakeMinor; // debit at open
    return this.activeBet('originals.mines', input, {
      status: 'ACTIVE',
      k: 0,
      revealed: [],
      nextMultiplier: minesMultiplier(mines, 1, 0.01),
    });
  }

  private stepMines(round: MinesRound, action: ActionRequest): ActionResult {
    const roundId = action.roundId;
    if (action.op === 'cashout') {
      const k = round.revealed.length;
      const multiplier = minesMultiplier(round.mines, k, round.edge);
      const payoutMinor = Math.floor(round.stakeMinor * multiplier);
      this.balanceMinor += payoutMinor; // credit at cashout
      this.rounds.delete(roundId);
      return {
        status: 'CASHED_OUT',
        k,
        multiplier,
        minePositions: round.minePositions,
        roundId,
        payoutMinor,
      };
    }
    const cell = Number(action.cell);
    if (round.minePositions.includes(cell)) {
      this.rounds.delete(roundId);
      return {
        cell,
        safe: false,
        k: round.revealed.length,
        status: 'LOST',
        minePositions: round.minePositions,
        roundId,
      };
    }
    if (!round.revealed.includes(cell)) round.revealed.push(cell);
    const k = round.revealed.length;
    const currentMultiplier = minesMultiplier(round.mines, k, round.edge);
    const nextMultiplier =
      k < 25 - round.mines
        ? minesMultiplier(round.mines, k + 1, round.edge)
        : null;
    return {
      cell,
      safe: true,
      k,
      currentMultiplier,
      nextMultiplier,
      status: 'ACTIVE',
      roundId,
    };
  }

  private openHilo(input: BetRequest): BetObject {
    const shownRank = 1 + Math.floor(Math.random() * 13);
    this.rounds.set(input.betId, {
      kind: 'hilo',
      shownRank,
      cumulative: 1,
      steps: 0,
      stakeMinor: input.stakeMinor,
      edge: 0.01,
    });
    this.lastRound = { gameId: 'originals.hilo', roundId: input.betId };
    this.balanceMinor -= input.stakeMinor; // debit at open
    return this.activeBet('originals.hilo', input, {
      status: 'ACTIVE',
      shownRank,
      currentMultiplier: 1,
      steps: 0,
    });
  }

  private stepHilo(round: HiloRound, action: ActionRequest): ActionResult {
    const roundId = action.roundId;
    if (action.op === 'cashout') {
      const payoutMinor = Math.floor(round.stakeMinor * round.cumulative);
      this.balanceMinor += payoutMinor; // credit at cashout
      this.rounds.delete(roundId);
      return {
        status: 'CASHED_OUT',
        multiplier: round.cumulative,
        currentMultiplier: round.cumulative,
        steps: round.steps,
        roundId,
        payoutMinor,
      };
    }
    const side = action.side === 'LOWER' ? 'LOWER' : 'HIGHER';
    const shown = round.shownRank;
    const next = 1 + Math.floor(Math.random() * 13);
    const won = side === 'HIGHER' ? next >= shown : next <= shown;
    if (!won) {
      this.rounds.delete(roundId);
      return {
        status: 'LOST',
        side,
        guessedFromRank: shown,
        revealedRank: next,
        won: false,
        steps: round.steps,
        roundId,
      };
    }
    const sm = hiloStepMult(shown, side, round.edge);
    round.cumulative *= sm;
    round.steps += 1;
    round.shownRank = next;
    return {
      status: 'ACTIVE',
      side,
      guessedFromRank: shown,
      revealedRank: next,
      shownRank: next,
      won: true,
      stepMultiplier: sm,
      currentMultiplier: round.cumulative,
      steps: round.steps,
      roundId,
    };
  }

  // Build the ACTIVE bet envelope returned when a stateful round OPENS (no settle:
  // status ACTIVE, outcome = the game's open public_view snapshot, settledAt null).
  private activeBet(
    gameId: string,
    input: BetRequest,
    outcome: Record<string, unknown>,
  ): BetObject {
    const now = new Date().toISOString();
    return {
      betId: input.betId,
      gameId,
      userId: 'demo-user',
      walletId: 'demo-wallet',
      currency: input.currency,
      mode: input.mode,
      stakeMinor: input.stakeMinor,
      status: 'ACTIVE',
      fairness: {
        serverSeedHash: 'demo-unverified',
        clientSeed: this.demoId('seed'),
        nonce: Math.floor(Math.random() * 1_000_000),
      },
      input: input.input ?? {},
      outcome,
      createdAt: now,
      settledAt: null,
      idempotencyKeys: { settle: `demo-${input.betId}` },
    };
  }

  private fakeGeneric(input: BetRequest): Record<string, unknown> {
    const won = Math.random() < 0.49;
    const multiplier = won ? 1 + Math.random() * 2 : 0;
    const payoutMinor = Math.floor(input.stakeMinor * multiplier);
    return { demo: true, won, multiplier, payoutMinor };
  }

  private demoId(prefix: string): string {
    return `demo-${prefix}-${Math.floor(Math.random() * 1e9).toString(36)}`;
  }
}

// --- demo-only stateful math (mirrors the engine formulas; fabrication, not server) --- //

/** C(n, k) — the binomial coefficient (small n here, plain float product). */
function comb(n: number, k: number): number {
  if (k < 0 || k > n) return 0;
  let r = 1;
  for (let i = 0; i < k; i++) r = (r * (n - i)) / (i + 1);
  return r;
}

/** Mines settlement multiplier after k safe reveals (spec §A.6):
 *  (1 − edge)·C(25,k)/C(25−M,k); 1.0 at k=0. */
function minesMultiplier(mines: number, k: number, edge: number): number {
  if (k === 0) return 1;
  return ((1 - edge) * comb(25, k)) / comb(25 - mines, k);
}

/** HiLo step multiplier (spec §A.7): (1 − edge)/p(chosen side), tie wins either side. */
function hiloStepMult(rank: number, side: string, edge: number): number {
  const p = side === 'HIGHER' ? (14 - rank) / 13 : rank / 13;
  return (1 - edge) / p;
}
