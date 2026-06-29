import { afterEach, describe, expect, it, vi } from 'vitest';
import { HttpGameClient } from './HttpGameClient';
import { createGameClient } from './index';
import { MockGameClient } from './mock/MockGameClient';

// The factory is the ONE place that knows the base URL: VITE_API_BASE_URL present →
// real Http transport; absent → the quarantined Mock. Every component depends on the
// returned GameClient, never on this choice.
describe('createGameClient', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('returns the MockGameClient when VITE_API_BASE_URL is unset', () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    const client = createGameClient();
    expect(client).toBeInstanceOf(MockGameClient);
    expect(client.isDemo).toBe(true);
  });

  it('returns the HttpGameClient when VITE_API_BASE_URL is set', () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test');
    const client = createGameClient();
    expect(client).toBeInstanceOf(HttpGameClient);
    expect(client.isDemo).toBe(false);
  });
});
