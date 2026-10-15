export const runKeys = {
  all: (id: string) => ['run', id] as const,
  state: (id: string) => ['run', id, 'state'] as const,
  events: (id: string) => ['run', id, 'events'] as const,
  actions: (id: string) => ['run', id, 'actions'] as const,
  artifacts: (id: string) => ['run', id, 'artifacts'] as const,
  evaluations: (id: string) => ['run', id, 'evaluations'] as const,
};

/** Link helpers for deep links inside the run detail page (used by evidence links). */
export const runLinks = {
  event: (runId: string, seq: string | number) => `/runs/${encodeURIComponent(runId)}?tab=trace&seq=${encodeURIComponent(String(seq))}`,
  message: (runId: string, messageId: string) =>
    `/runs/${encodeURIComponent(runId)}?tab=mailboxes&message=${encodeURIComponent(messageId)}`,
  action: (runId: string, ref: string) => `/runs/${encodeURIComponent(runId)}?tab=actions&action=${encodeURIComponent(ref)}`,
  state: (runId: string) => `/runs/${encodeURIComponent(runId)}?tab=mailboxes`,
  artifact: (runId: string, ref: string) => `/runs/${encodeURIComponent(runId)}?tab=artifacts&ref=${encodeURIComponent(ref)}`,
};
