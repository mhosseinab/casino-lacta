import { afterEach, describe, expect, it, vi } from 'vitest';
import type { BetObject, GuestSessionResponse } from '../../contracts';
import { HttpGameClient } from './HttpGameClient';

const BASE = 'https://api.example.test';

const guest: GuestSessionResponse = {
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  tokens: {
    accessToken: 'tok-abc',
    refreshToken: 'ref-xyz',
    tokenType: 'bearer',
  },
};

const betBody: BetObject = {
  betId: 'b1',
  gameId: 'originals.dice',
  userId: 'u1',
  walletId: 'w1',
  currency: 'GOLD',
  mode: 'PLAY',
  stakeMinor: 100,
  status: 'SETTLED',
  fairness: { serverSeedHash: 'h', clientSeed: 'c', nonce: 0 },
  input: {},
  outcome: { won: true },
  createdAt: null,
  settledAt: null,
  idempotencyKeys: {},
};

const betReq = {
  betId: 'b1',
  stakeMinor: 100,
  currency: 'GOLD',
  mode: 'PLAY',
  input: {},
};

function mockResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('HttpGameClient — it only transports', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('bet() calls the right URL/method with a Bearer header and returns the body UNCHANGED', async () => {
    const fetchMock = vi.fn(
      (input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/auth/guest'))
          return Promise.resolve(mockResponse(200, guest));
        return Promise.resolve(mockResponse(200, betBody));
      },
    );
    vi.stubGlobal('fetch', fetchMock);

    const client = new HttpGameClient(BASE);
    await client.startGuestSession();
    const result = await client.bet('originals.dice', betReq);

    // Returned UNCHANGED — no outcome/transformation logic in the transport.
    expect(result).toEqual(betBody);

    const betCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).endsWith('/games/originals.dice/bet'),
    );
    expect(betCall).toBeDefined();
    if (!betCall) throw new Error('expected a /bet call');
    const [url, init] = betCall;
    expect(url).toBe(`${BASE}/games/originals.dice/bet`);
    expect(init?.method).toBe('POST');
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer tok-abc');
    expect(JSON.parse(String(init?.body))).toEqual(betReq);
  });

  it('refreshes the token on 401 and retries the request with the new Bearer', async () => {
    let betCalls = 0;
    const fetchMock = vi.fn(
      (input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => {
        const url = String(input);
        if (url.endsWith('/auth/guest'))
          return Promise.resolve(mockResponse(200, guest));
        if (url.endsWith('/auth/refresh'))
          return Promise.resolve(
            mockResponse(200, { accessToken: 'tok-new', tokenType: 'bearer' }),
          );
        // /bet: first attempt 401, retry succeeds.
        betCalls += 1;
        if (betCalls === 1) return Promise.resolve(mockResponse(401, {}));
        return Promise.resolve(mockResponse(200, betBody));
      },
    );
    vi.stubGlobal('fetch', fetchMock);

    const client = new HttpGameClient(BASE);
    await client.startGuestSession();
    const result = await client.bet('originals.dice', betReq);
    expect(result).toEqual(betBody);

    const refreshCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).endsWith('/auth/refresh'),
    );
    expect(refreshCall).toBeDefined();

    const betAttempts = fetchMock.mock.calls.filter((c) =>
      String(c[0]).endsWith('/bet'),
    );
    expect(betAttempts.length).toBe(2);
    const retried = betAttempts[1];
    const headers = retried[1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer tok-new');
  });

  it('is not a demo transport', () => {
    expect(new HttpGameClient(BASE).isDemo).toBe(false);
  });
});
