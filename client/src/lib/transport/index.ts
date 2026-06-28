import {
  type ReactNode,
  createContext,
  createElement,
  useContext,
  useMemo,
} from 'react';
import type { GameClient } from './GameClient';
import { HttpGameClient } from './HttpGameClient';
import { MockGameClient } from './mock/MockGameClient';

export type {
  ActionResult,
  CrashCrashEvent,
  CrashEvent,
  CrashRoundEvent,
  CrashSocketHandlers,
  CrashSubscription,
  CrashTickEvent,
  FairnessResult,
  GameClient,
  GameStateProjection,
} from './GameClient';
export { CRASH_WS_PATH } from './GameClient';
export { HttpGameClient } from './HttpGameClient';
export { MockGameClient } from './mock/MockGameClient';

// The ONE place that knows the base URL (plan §4.1): VITE_API_BASE_URL present → the
// real Http transport; absent → the quarantined demo Mock. Read at call time (not at
// module load) so tests can stub the env.
export function createGameClient(): GameClient {
  const baseUrl = import.meta.env.VITE_API_BASE_URL;
  return baseUrl ? new HttpGameClient(baseUrl) : new MockGameClient();
}

const GameClientContext = createContext<GameClient | null>(null);

// Provider — defaults to the factory's choice; an explicit `client` overrides it
// (used by tests/storybook to inject a fake without touching the env).
export function GameClientProvider(props: {
  client?: GameClient;
  children: ReactNode;
}): ReactNode {
  const value = useMemo(
    () => props.client ?? createGameClient(),
    [props.client],
  );
  return createElement(GameClientContext.Provider, { value }, props.children);
}

export function useGameClient(): GameClient {
  const client = useContext(GameClientContext);
  if (!client) {
    throw new Error('useGameClient must be used within a GameClientProvider');
  }
  return client;
}
