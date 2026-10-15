import { useQuery } from '@tanstack/react-query';
import type { SystemStatus } from '../api/types';
import { useApi } from '../auth/session';

/** React Query key of GET /v1/system/status (shared by the header pill and Connect). */
export const systemStatusKey = ['system', 'status'] as const;

export interface StatusSummary {
  mode: string;
  llm: string;
  listeners: string;
  /** Every listener is up. */
  listenersUp: boolean;
}

/** Plain-text summary for the header pill (never colour alone). */
export function summarizeStatus(s: SystemStatus): StatusSummary {
  const imap = s.listeners.imap?.running ?? false;
  const smtp = s.listeners.smtp?.running ?? false;
  const word = (up: boolean) => (up ? 'up' : 'down');
  return {
    mode: s.local_no_auth ? 'Local no-auth' : 'Key mode',
    llm: `LLM: ${s.llm_provider}${s.llm_model ? ` (${s.llm_model})` : ''}`,
    listeners: `IMAP ${word(imap)} · SMTP ${word(smtp)}`,
    listenersUp: imap && smtp,
  };
}

/** GET /v1/system/status, cached for 30 s and shared by every caller. */
export function useSystemStatus() {
  const api = useApi();
  return useQuery({ queryKey: systemStatusKey, queryFn: () => api.systemStatus(), retry: false, staleTime: 30_000 });
}
