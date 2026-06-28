import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react';
import { useGameClient } from './transport';

// The session/balance store (plan §3). It holds ONLY server-authoritative fields:
// the balance + currency come straight from GET /auth/me and are never computed,
// accumulated, or adjusted client-side (CLAUDE.md "balance is a server field").
// After a settle, callers invoke refreshBalance() to RE-FETCH /me — the client
// never derives the new balance from the bet result itself.

export interface SessionState {
  /** Server balance in integer minor units; null until the first /me resolves. */
  balanceMinor: number | null;
  /** The wallet currency reported by the server (e.g. "GOLD"). */
  currency: string;
  /** Re-fetch GET /auth/me — call after a settle to reflect the new balance. */
  refreshBalance: () => Promise<void>;
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider(props: { children: ReactNode }): ReactNode {
  const client = useGameClient();
  const [balanceMinor, setBalanceMinor] = useState<number | null>(null);
  const [currency, setCurrency] = useState<string>('GOLD');

  const refreshBalance = useCallback(async () => {
    const me = await client.me();
    setBalanceMinor(me.balanceMinor);
    setCurrency(me.currency);
  }, [client]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // Bootstrap: fund a guest, then read the server-authoritative balance.
      await client.startGuestSession();
      const me = await client.me();
      if (!cancelled) {
        setBalanceMinor(me.balanceMinor);
        setCurrency(me.currency);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client]);

  return (
    <SessionContext.Provider value={{ balanceMinor, currency, refreshBalance }}>
      {props.children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const session = useContext(SessionContext);
  if (!session) {
    throw new Error('useSession must be used within a SessionProvider');
  }
  return session;
}
