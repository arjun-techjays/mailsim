import { apiRequest, type ApiConfig, type RequestOptions } from './client';
import type {
  AnswerKeyOut,
  ArtifactOut,
  BuildComparison,
  ClockResult,
  EvaluationReport,
  EvaluationRequest,
  EventPage,
  InjectRequest,
  InjectResult,
  ReplayOut,
  RunCreate,
  RunCreated,
  RunOut,
  ScenarioOut,
} from './types';
import type { ActionRecord } from './types';

const seg = encodeURIComponent;

export interface EventFilters {
  type?: string;
  actor?: string;
  epoch?: string;
}

/** Shape of the planned GET /v1/me; only `role` is relied upon. */
export interface Me {
  role?: unknown;
  [key: string]: unknown;
}

/** One function per OpenAPI operation the dashboard uses. */
export function createApi(cfg: ApiConfig) {
  const get = <T>(path: string, query?: RequestOptions['query'], signal?: AbortSignal) =>
    apiRequest<T>(cfg, 'GET', path, { query, signal });
  const post = <T>(path: string, body?: unknown, idempotent = false) =>
    apiRequest<T>(cfg, 'POST', path, { body, idempotent });
  const run = (id: string) => `/v1/runs/${seg(id)}`;

  return {
    health: () => get<Record<string, string>>('/v1/health'),
    /** Additive endpoint (may be absent: 404/501). */
    me: () => get<Me>('/v1/me'),
    /** Additive endpoint (may be absent: 404/501). Accepts a page or a bare array. */
    listRuns: async (cursor: string | null): Promise<{ items: RunOut[]; next_cursor: string | null }> => {
      const out = await get<RunOut[] | { items?: RunOut[]; next_cursor?: string | null }>('/v1/runs', { cursor });
      if (Array.isArray(out)) return { items: out, next_cursor: null };
      return { items: out?.items ?? [], next_cursor: out?.next_cursor ?? null };
    },

    // scenarios
    listScenarios: (filters: { tag?: string; q?: string } = {}, signal?: AbortSignal) =>
      get<ScenarioOut[]>('/v1/scenarios', filters, signal),
    getScenario: (key: string) => get<ScenarioOut>(`/v1/scenarios/${seg(key)}`),
    listAnswerKeys: (key: string) => get<AnswerKeyOut[]>(`/v1/scenarios/${seg(key)}/answer-keys`),
    createRun: (key: string, body: RunCreate) =>
      post<RunCreated>(`/v1/scenarios/${seg(key)}/runs`, body, true),
    accuracy: (key: string, params: { answerKeyVersion?: number; builds?: string[]; baseline?: string }) =>
      get<BuildComparison>(`/v1/scenarios/${seg(key)}/accuracy`, {
        answer_key_version: params.answerKeyVersion,
        builds: params.builds && params.builds.length > 0 ? params.builds : undefined,
        baseline: params.baseline,
      }),

    // runs (control plane)
    getRun: (id: string) => get<RunOut>(run(id)),
    startRun: (id: string) => post<RunOut>(`${run(id)}/start`),
    cancelRun: (id: string) => post<RunOut>(`${run(id)}/cancel`, undefined, true),
    resetRun: (id: string) => post<RunOut>(`${run(id)}/reset`, undefined, true),
    replayRun: (id: string) => post<ReplayOut>(`${run(id)}/replay`, undefined, true),
    /** type/actor/epoch are additive filters; servers that ignore them still return valid pages. */
    events: (id: string, cursor: string | null, pageSize: number, filters: EventFilters = {}) =>
      get<EventPage>(`${run(id)}/events`, { cursor, page_size: pageSize, ...filters }),
    artifacts: (id: string) => get<ArtifactOut[]>(`${run(id)}/artifacts`),

    // simulator control
    advance: (id: string, seconds: number) =>
      post<ClockResult>(`${run(id)}/clock/advance`, { seconds }, true),
    tick: (id: string) => post<ClockResult>(`${run(id)}/scheduler/tick`),
    inject: (id: string, mailboxId: string, body: InjectRequest) =>
      post<InjectResult>(`${run(id)}/mailboxes/${seg(mailboxId)}/inject`, body, true),
    state: (id: string) => get<Record<string, unknown>>(`${run(id)}/state`),
    actions: (id: string) => get<ActionRecord[]>(`${run(id)}/actions`),

    // evaluation (evaluator role)
    listEvaluations: (id: string) => get<EvaluationReport[]>(`${run(id)}/evaluations`),
    evaluate: (id: string, body: EvaluationRequest) =>
      post<EvaluationReport>(`${run(id)}/evaluations`, body, true),
  };
}

export type Api = ReturnType<typeof createApi>;
