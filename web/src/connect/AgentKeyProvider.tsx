import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { AgentKeyContext, type AgentKeyStore, type HeldKey } from './agentKeys';

/** In-memory agent keys of this tab (see agentKeys.ts). */
export function AgentKeyProvider({ children }: { children: ReactNode }) {
  const [keys, setKeys] = useState<Record<string, HeldKey>>({});
  const set = useCallback((runId: string, key: HeldKey) => setKeys((k) => ({ ...k, [runId]: key })), []);
  const forget = useCallback(
    (runId: string) =>
      setKeys((k) => {
        const { [runId]: _gone, ...rest } = k;
        return rest;
      }),
    [],
  );
  const store = useMemo<AgentKeyStore>(() => ({ get: (runId) => keys[runId], set, forget }), [keys, set, forget]);
  return <AgentKeyContext.Provider value={store}>{children}</AgentKeyContext.Provider>;
}
