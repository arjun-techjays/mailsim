import { apiRequest, apiText, type ApiConfig, type RequestOptions } from './client';
import type {
  ActionOut,
  MeOut,
  ReportFormat,
  RunPage,
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
  // connect & status (UX-F3)
  AgentCredential,
  ConnectionTestRequest,
  ConnectionTestResult,
  RunConnection,
  SystemStatus,
} from './types';

const seg = encodeURIComponent;

export interface EventFilters {
  type?: string;
  actor?: string;
  epoch?: string;
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
    /** Caller identity (older servers without it answer 404/501; callers fall back). */
    me: () => get<MeOut>('/v1/me'),
    /** Runs of the caller's project, newest first. */
    listRuns: (cursor: string | null, filters: { scenario_key?: string; status?: string } = {}) =>
      get<RunPage>('/v1/runs', { cursor, ...filters }),
    /** Evaluation report export; returned as text for download. */
    report: (runId: string, evaluationId: string, format: ReportFormat) =>
      apiText(cfg, `${run(runId)}/evaluations/${seg(evaluationId)}/report`, { format }),

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
    actions: (id: string) => get<ActionOut[]>(`${run(id)}/actions`),

    // evaluation (evaluator role)
    listEvaluations: (id: string) => get<EvaluationReport[]>(`${run(id)}/evaluations`),
    evaluate: (id: string, body: EvaluationRequest) =>
      post<EvaluationReport>(`${run(id)}/evaluations`, body, true),

    // ---- connect & status (UX-F3)
    /** Server mode, LLM provider, served interfaces and IMAP/SMTP listener status. */
    systemStatus: () => get<SystemStatus>('/v1/system/status'),
    /** How an agent connects to a run (endpoints, mailbox, key ids; never secrets). */
    runConnection: (id: string) => get<RunConnection>(`${run(id)}/connection`),
    /** Real call through one interface with the given agent key, made by the server. */
    testConnection: (id: string, body: ConnectionTestRequest) =>
      post<ConnectionTestResult>(`${run(id)}/connection-tests`, body),
    /** Mints a new agent key (shown once); the server revokes the run's earlier agent keys. */
    mintAgentKey: (id: string) => post<AgentCredential>(`${run(id)}/agent-credentials`),
    revokeKey: (keyId: string) => apiRequest<void>(cfg, 'DELETE', `/v1/keys/${seg(keyId)}`),
  };
}

export type Api = ReturnType<typeof createApi>;
