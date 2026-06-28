import type {
  AccessToken,
  ActionRequest,
  BetObject,
  BetRequest,
  FairnessDisclosure,
  GuestSessionResponse,
  MeResponse,
} from '../../contracts';
import {
  type ActionResult,
  CRASH_WS_PATH,
  type CrashEvent,
  type CrashSocketHandlers,
  type CrashSubscription,
  type FairnessResult,
  type GameClient,
  type GameStateProjection,
} from './GameClient';

// The real transport. It ONLY transports: it never inspects, mutates, or derives an
// outcome/balance — it sends intent, returns the server's body UNCHANGED, and manages
// the in-memory Bearer token (refresh on 401). The server decides everything.
export class HttpGameClient implements GameClient {
  readonly isDemo = false;

  private readonly baseUrl: string;
  private accessToken: string | null = null;
  private refreshToken: string | null = null;

  constructor(baseUrl: string) {
    // Normalise: drop a trailing slash so `${baseUrl}/auth/guest` never doubles up.
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  async startGuestSession(): Promise<GuestSessionResponse> {
    const res = await fetch(`${this.baseUrl}/auth/guest`, { method: 'POST' });
    const session = (await res.json()) as GuestSessionResponse;
    this.accessToken = session.tokens.accessToken;
    this.refreshToken = session.tokens.refreshToken;
    return session;
  }

  async me(): Promise<MeResponse> {
    const res = await this.authedFetch('/auth/me', { method: 'GET' });
    return (await res.json()) as MeResponse;
  }

  async bet(gameId: string, input: BetRequest): Promise<BetObject> {
    const res = await this.authedFetch(
      `/games/${gameId}/bet`,
      this.jsonInit('POST', input),
    );
    return (await res.json()) as BetObject;
  }

  async spin(gameId: string, input: BetRequest): Promise<BetObject> {
    const res = await this.authedFetch(
      `/games/${gameId}/spin`,
      this.jsonInit('POST', input),
    );
    return (await res.json()) as BetObject;
  }

  async action(
    gameId: string,
    actionInput: ActionRequest,
  ): Promise<ActionResult> {
    const res = await this.authedFetch(
      `/games/${gameId}/action`,
      this.jsonInit('POST', actionInput),
    );
    return (await res.json()) as ActionResult;
  }

  async state(gameId: string): Promise<GameStateProjection> {
    const res = await this.authedFetch(`/games/${gameId}/state`, {
      method: 'GET',
    });
    return (await res.json()) as GameStateProjection;
  }

  async fairness(betId: string): Promise<FairnessResult> {
    const res = await this.authedFetch(
      `/fairness/${encodeURIComponent(betId)}`,
      { method: 'GET' },
    );
    const disclosure = (await res.json()) as FairnessDisclosure;
    return { available: true, disclosure };
  }

  crashSocket(handlers: CrashSocketHandlers): CrashSubscription {
    const ws = new WebSocket(`${this.wsBaseUrl()}${CRASH_WS_PATH}`);
    ws.onopen = () => handlers.onOpen?.();
    ws.onmessage = (ev: MessageEvent) => {
      handlers.onEvent(JSON.parse(String(ev.data)) as CrashEvent);
    };
    ws.onclose = (ev: CloseEvent) =>
      handlers.onClose?.({ code: ev.code, reason: ev.reason });
    ws.onerror = (err) => handlers.onError?.(err);
    return { close: () => ws.close() };
  }

  // --- internals: token + request plumbing (no game logic) --------------------- //

  private jsonInit(method: string, body: unknown): RequestInit {
    return {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    };
  }

  private wsBaseUrl(): string {
    return this.baseUrl.replace(/^http/, 'ws');
  }

  // Sends a request with the current Bearer token; on 401 re-authenticates (refresh,
  // falling back to a fresh guest session) and retries ONCE.
  private async authedFetch(
    path: string,
    init: RequestInit,
  ): Promise<Response> {
    const send = (): Promise<Response> => {
      const headers: Record<string, string> = {
        ...(init.headers as Record<string, string>),
      };
      if (this.accessToken)
        headers.Authorization = `Bearer ${this.accessToken}`;
      return fetch(`${this.baseUrl}${path}`, { ...init, headers });
    };
    let res = await send();
    if (res.status === 401) {
      await this.reauth();
      res = await send();
    }
    return res;
  }

  private async reauth(): Promise<void> {
    if (this.refreshToken) {
      const res = await fetch(`${this.baseUrl}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refreshToken: this.refreshToken }),
      });
      if (res.ok) {
        const data = (await res.json()) as AccessToken;
        this.accessToken = data.accessToken;
        return;
      }
    }
    // No refresh token, or refresh rejected → start a fresh guest session.
    await this.startGuestSession();
  }
}
