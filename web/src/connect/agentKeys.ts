import { createContext, useContext } from 'react';

/**
 * An agent key held by this tab, per run. Keys are shown once by the API, so the dashboard
 * keeps the one it just received (run creation or minting) in React memory only: never in
 * the URL, history state, sessionStorage or localStorage. A reload forgets it.
 */
export interface HeldKey {
  apiKey: string;
  mailboxAddress: string;
  mailboxId: string;
}

export interface AgentKeyStore {
  get: (runId: string) => HeldKey | undefined;
  set: (runId: string, key: HeldKey) => void;
  forget: (runId: string) => void;
}

export const AgentKeyContext = createContext<AgentKeyStore | null>(null);

export function useAgentKeys(): AgentKeyStore {
  const v = useContext(AgentKeyContext);
  if (!v) throw new Error('useAgentKeys must be used inside <AgentKeyProvider>');
  return v;
}

/** `msk_0123456789ab_secret` -> `msk_0123456789ab_••••••••` (prefix only, never the secret). */
export function maskKey(key: string): string {
  const m = /^(msk_[^_]+_)/.exec(key);
  return m ? `${m[1]}••••••••` : '••••••••';
}

/** React Query key of GET /v1/runs/{id}/connection. */
export const connectionKey = (runId: string) => ['run', runId, 'connection'] as const;
