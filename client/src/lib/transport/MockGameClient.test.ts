import { describe, expect, it } from 'vitest';
import { MockGameClient } from './mock/MockGameClient';

// The mock is a quarantined DEMO transport: it fabricates plausible, contract-shaped
// objects so the static Pages demo plays without a server. It must NEVER claim a real
// provably-fair proof (that is server truth) — fairness() reports "unavailable".
describe('MockGameClient', () => {
  const client = new MockGameClient();

  it('is a demo transport', () => {
    expect(client.isDemo).toBe(true);
  });

  it('startGuestSession() returns a contract-shaped GuestSessionResponse', async () => {
    const session = await client.startGuestSession();
    expect(typeof session.userId).toBe('string');
    expect(typeof session.walletId).toBe('string');
    expect(typeof session.tokens.accessToken).toBe('string');
    expect(typeof session.tokens.refreshToken).toBe('string');
  });

  it('me() returns a contract-shaped MeResponse with an integer balance', async () => {
    const me = await client.me();
    expect(typeof me.userId).toBe('string');
    expect(Number.isInteger(me.balanceMinor)).toBe(true);
  });

  it('bet() returns a contract-shaped BetObject echoing the requested game + stake', async () => {
    const bet = await client.bet('originals.dice', {
      betId: 'b1',
      stakeMinor: 100,
      currency: 'GOLD',
      mode: 'PLAY',
      input: {},
    });
    expect(typeof bet.betId).toBe('string');
    expect(bet.gameId).toBe('originals.dice');
    expect(bet.stakeMinor).toBe(100);
    expect(typeof bet.status).toBe('string');
    expect(typeof bet.fairness.serverSeedHash).toBe('string');
    expect(typeof bet.fairness.clientSeed).toBe('string');
    expect(typeof bet.fairness.nonce).toBe('number');
    expect(bet.idempotencyKeys).toBeDefined();
  });

  it('spin() returns a contract-shaped BetObject', async () => {
    const bet = await client.spin('slots.demo', {
      betId: 's1',
      stakeMinor: 50,
      currency: 'GOLD',
      mode: 'PLAY',
      input: {},
    });
    expect(bet.gameId).toBe('slots.demo');
    expect(bet.stakeMinor).toBe(50);
  });

  it('fairness() reports unavailable in demo (NO fabricated proof)', async () => {
    const result = await client.fairness('b1');
    expect(result.available).toBe(false);
    if (!result.available) {
      expect(typeof result.reason).toBe('string');
      expect(result.reason.length).toBeGreaterThan(0);
    }
  });
});
