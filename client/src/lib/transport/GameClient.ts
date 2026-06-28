// The transport seam (plan §4.1). Every game view depends ONLY on this interface —
// never on `fetch`/`WebSocket` directly. Two adapters implement it (HttpGameClient,
// MockGameClient); a factory (./index) picks one by config. All payloads/returns use
// the GENERATED @casino/contracts types (re-exported via ../../contracts), so the
// client never hand-rolls an API shape.
import type {
  ActionRequest,
  BetObject,
  BetRequest,
  FairnessDisclosure,
  GuestSessionResponse,
  MeResponse,
} from '../../contracts';

// The shared Crash WS path (mirrors app/ws/crash.py CRASH_WS_PATH). REST base URL is
// turned into a ws:// URL by the Http adapter.
export const CRASH_WS_PATH = '/games/originals.crash';

// Stateful-game projections (/state, /action) are typed `{[k]: unknown}` in the
// OpenAPI schema — the server returns the safe client-facing projection only. We keep
// that shape verbatim rather than inventing a per-game type the contract doesn't have.
export type GameStateProjection = { [key: string]: unknown };
export type ActionResult = { [key: string]: unknown };

// fairness() is a union, not a throw: the real adapter returns the server's disclosure;
// the demo adapter CANNOT produce a real provably-fair proof, so it reports unavailable
// instead of fabricating one (iron rule: the server decides — and proves — outcomes).
export type FairnessResult =
  | { available: true; disclosure: FairnessDisclosure }
  | { available: false; reason: string };

// Crash WS events are NOT part of the OpenAPI schema (they are realtime messages, not
// REST bodies), so this union is the one unavoidable hand-rolled shape. It mirrors the
// per-status payload builders in app/ws/crash.py (_round_event/_tick_event/_crash_event)
// field-for-field.
export interface CrashRoundEvent {
  type: 'round';
  roundId: string;
  roundNumber: number;
  serverSeedHash: string;
  status: string;
}
export interface CrashTickEvent {
  type: 'tick';
  roundId: string;
  roundNumber: number;
  multiplier: number;
}
export interface CrashCrashEvent {
  type: 'crash';
  roundId: string;
  roundNumber: number;
  crashPoint: number;
  serverSeed: string;
  serverSeedHash: string;
  status: string;
}
export type CrashEvent = CrashRoundEvent | CrashTickEvent | CrashCrashEvent;

export interface CrashSocketHandlers {
  onEvent: (event: CrashEvent) => void;
  onOpen?: () => void;
  onClose?: (info: { code: number; reason: string }) => void;
  onError?: (error: unknown) => void;
}

export interface CrashSubscription {
  close: () => void;
}

export interface GameClient {
  /** True for the quarantined demo transport (drives the persistent "DEMO" banner). */
  readonly isDemo: boolean;

  /** POST /auth/guest — funds a play-money guest and stores its tokens in memory. */
  startGuestSession(): Promise<GuestSessionResponse>;

  /** GET /auth/me — the server-authoritative balance + identity projection. */
  me(): Promise<MeResponse>;

  /** POST /games/{gameId}/bet — instant settle through the shared bet loop. */
  bet(gameId: string, input: BetRequest): Promise<BetObject>;

  /** POST /games/{gameId}/spin — slots synonym for /bet (same loop). */
  spin(gameId: string, input: BetRequest): Promise<BetObject>;

  /** POST /games/{gameId}/action — advance a stateful round (Mines/HiLo). */
  action(gameId: string, actionInput: ActionRequest): Promise<ActionResult>;

  /** GET /games/{gameId}/state — resume the caller's most recent round. */
  state(gameId: string): Promise<GameStateProjection>;

  /** GET /fairness/{betId} — the provably-fair disclosure, or "unavailable in demo". */
  fairness(betId: string): Promise<FairnessResult>;

  /** Subscribe to the shared Crash round/tick/crash stream; returns a closable handle. */
  crashSocket(handlers: CrashSocketHandlers): CrashSubscription;
}
