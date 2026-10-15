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
} from './types';
// ---- answer keys & mailbox browser (UX-F2)
import type {
  ActivityPage,
  AnswerKeyDocument,
  AnswerKeyValidation,
  AuthoringMeta,
  MailboxOut,
  MessagePage,
  ParsedDocument,
  RenderedYaml,
  ScenarioVersionDocument,
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

    // ---- answer keys & mailbox browser (UX-F2)
    authoringMeta: () => get<AuthoringMeta>('/v1/meta/authoring'),
    /** Controller only: the published (agent-facing) scenario document, for pickers. */
    scenarioVersionDocument: (key: string, version: number) =>
      get<ScenarioVersionDocument>(`/v1/scenarios/${seg(key)}/versions/${version}/document`),
    /** Evaluator only: the full answer key (never call with a controller-only session). */
    answerKeyDocument: (key: string, version: number) =>
      get<AnswerKeyDocument>(`/v1/scenarios/${seg(key)}/answer-keys/${version}/document`),
    validateAnswerKey: (
      key: string,
      body: { document?: Record<string, unknown>; source?: string },
      signal?: AbortSignal,
    ) => apiRequest<AnswerKeyValidation>(cfg, 'POST', `/v1/scenarios/${seg(key)}/answer-keys/validate`, { body, signal }),
    createAnswerKey: (key: string, body: { document?: Record<string, unknown>; source?: string }) =>
      post<AnswerKeyOut>(`/v1/scenarios/${seg(key)}/answer-keys`, body, true),
    jsonToYaml: (document: Record<string, unknown>) => post<RenderedYaml>('/v1/tools/json-to-yaml', { document }),
    yamlToJson: (source: string) => post<ParsedDocument>('/v1/tools/yaml-to-json', { source }),
    runMailboxes: (id: string) => get<MailboxOut[]>(`${run(id)}/mailboxes`),
    inspectMessages: (
      id: string,
      mailboxId: string,
      params: { folder?: string | null; cursor?: string | null; pageSize?: number; order?: 'asc' | 'desc' },
    ) =>
      get<MessagePage>(`${run(id)}/mailboxes/${seg(mailboxId)}/messages`, {
        folder: params.folder,
        cursor: params.cursor,
        page_size: params.pageSize,
        order: params.order,
      }),
    activity: (id: string, afterId: number, limit = 100) =>
      get<ActivityPage>(`${run(id)}/activity`, { after_id: afterId, limit }),
    // ---- end answer keys & mailbox browser (UX-F2)
  };
}

export type Api = ReturnType<typeof createApi>;
