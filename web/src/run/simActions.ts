// Simulator actions shared by the Overview controls, the run toolbar and the mailbox browser.
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { Api } from '../api/endpoints';
import type { ClockResult, InjectRequest, InjectResult } from '../api/types';
import { useApi } from '../auth/session';
import { runKeys } from './keys';

export type ClockOp = { kind: 'tick' } | { kind: 'advance'; seconds: number };

/** One clock step: a scheduler tick or advancing virtual time. */
export function clockCall(api: Api, runId: string, op: ClockOp): Promise<ClockResult> {
  return op.kind === 'tick' ? api.tick(runId) : api.advance(runId, op.seconds);
}

/** Inject a message into one of the run's mailboxes; refreshes the run views afterwards. */
export function useInjectMutation(runId: string) {
  const api = useApi();
  const qc = useQueryClient();
  const [result, setResult] = useState<InjectResult | null>(null);
  const mutation = useMutation({
    mutationFn: ({ mailboxId, body }: { mailboxId: string; body: InjectRequest }) => api.inject(runId, mailboxId, body),
    onMutate: () => setResult(null),
    onSuccess: (r) => setResult(r),
    onSettled: () => qc.invalidateQueries({ queryKey: runKeys.all(runId) }),
  });
  return { mutation, result };
}
