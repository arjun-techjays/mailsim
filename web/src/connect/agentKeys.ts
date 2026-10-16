import { createContext, useContext } from 'react';

/**
 * An agent key held by this tab, per run. Keys are shown once by the API, so the dashboard
 * keeps the one it just received (run creation or minting) in React memory only: never in
 * the URL, history state, sessionStorage or localStorage. A reload forgets it.
 */
export interface HeldKey {
  apiKey: string;
  /** 12-hex prefix of msk_<prefix>_<secret>, matched against KeyInfo.prefix; null if not that format. */
  prefix: string | null;
  mailboxAddress: string;
  mailboxId: string;
  /** Key id when the API named it (AgentCredential.key_id on newer servers). */
  keyId?: string | null;
}

/** AgentCredential.key_id where the server provides it (optional in the contract). */
export function credentialKeyId(cred: object): string | null {
  // SCHEMA-CAST: remove once schema.gen.ts (integration) has AgentCredential.key_id.
  const id = (cred as { key_id?: unknown }).key_id;
  return typeof id === 'string' && id ? id : null;
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

/** The public 12-hex prefix of an `msk_<prefix>_<secret>` key, or null. */
export function keyPrefix(key: string): string | null {
  return /^msk_([0-9a-f]{12})_/.exec(key)?.[1] ?? null;
}

/** `msk_0123456789ab_secret` -> `msk_0123456789ab_••••••••` (prefix only, never the secret). */
export function maskKey(key: string): string {
  const m = /^(msk_[^_]+_)/.exec(key);
  return m ? `${m[1]}••••••••` : '••••••••';
}

/** React Query key of GET /v1/runs/{id}/connection. */
export const connectionKey = (runId: string) => ['run', runId, 'connection'] as const;
