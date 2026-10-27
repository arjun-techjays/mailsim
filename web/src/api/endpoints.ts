import { apiBlob, apiRequest, apiText, buildUrl, longTimeoutMs, type ApiConfig, type RequestOptions } from './client';
import type {
  // connect & status (UX-F3)
  AgentCredential,
  ActionOut,
  ActivityPage,
  AnswerKeyDocument,
  AnswerKeyOut,
  AnswerKeyValidation,
  ArtifactOut,
  AuthoringMeta,
  BuildComparison,
  ClockResult,
  ComposedFixture,
  ComposeMessage,
  ConnectionTestRequest,
  ConnectionTestResult,
  DraftValidation,
  EmlSummary,
  EvaluationReport,
  EvaluationRequest,
  EventPage,
  InjectRequest,
  InjectResult,
  MailboxOut,
  HealthOut,
  MeOut,
  MessagePage,
  ParsedDocument,
  RenderedYaml,
  ReplayOut,
  ReportFormat,
  RunConnection,
  RunCreate,
  RunCreated,
  RunOut,
  RunPage,
  ScenarioCreate,
  ScenarioDraft,
  ScenarioOut,
  ScenarioVersionDocument,
  ScenarioVersionOut,
  SystemStatus,
} from './types';
// ---- builder (UX-F1)

// ---- end builder (UX-F1)
// ---- answer keys & mailbox browser (UX-F2)


const seg = encodeURIComponent;

/**
 * `path` (as listed by the server) re-expressed relative to the API base, or an error when it
 * would leave the API: another origin, outside `<base path>/v1/`, or encoded dot segments.
 */
export function apiRelativePath(baseUrl: string, path: string, here: string = window.location.href): string {
  const refuse = () => new Error(`Refusing to download from ${path}: not a file of this API.`);
  if (!path.startsWith('/') || /%2e/i.test(path) || /[\\]/.test(path)) throw refuse();
  // An empty base URL means "this dashboard's origin" (the dev/preview proxy), path prefix none.
  const base = new URL(baseUrl.trim() || '/', here);
  const basePath = base.pathname.replace(/\/+$/, '');
  const target = new URL(buildUrl(baseUrl, path), here);
  const inside = target.origin === base.origin && target.pathname.startsWith(`${basePath}/v1/`);
  if (!inside || target.search || target.hash) throw refuse();
  return target.pathname.slice(basePath.length);
}

/** One user action's retry identity: reuse `idempotencyKey` for every attempt of that action. */
export interface ActionOptions {
  idempotencyKey?: string;
}

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
    health: () => get<HealthOut>('/v1/health'),
    /** Caller identity (older servers without it answer 404/501; callers fall back). */
    me: () => get<MeOut>('/v1/me'),
    /** Runs of the caller's project, newest first. */
    listRuns: (
      cursor: string | null,
      filters: { scenario_key?: string; status?: string; page_size?: number; include_replays?: boolean } = {},
    ) => get<RunPage>('/v1/runs', { cursor, ...filters, include_replays: filters.include_replays ? 'true' : undefined }),
    /** Evaluation report export; returned as text for download. */
    report: (runId: string, evaluationId: string, format: ReportFormat) =>
      apiText(cfg, `${run(runId)}/evaluations/${seg(evaluationId)}/report`, { format }),

    // scenarios
    listScenarios: (filters: { tag?: string; q?: string } = {}, signal?: AbortSignal) =>
      get<ScenarioOut[]>('/v1/scenarios', filters, signal),
    getScenario: (key: string) => get<ScenarioOut>(`/v1/scenarios/${seg(key)}`),
    listAnswerKeys: (key: string) => get<AnswerKeyOut[]>(`/v1/scenarios/${seg(key)}/answer-keys`),
    /** Slow (seeds every mailbox): long timeout; pass one Idempotency-Key per user action. */
    createRun: (key: string, body: RunCreate, opts: ActionOptions = {}) =>
      apiRequest<RunCreated>(cfg, 'POST', `/v1/scenarios/${seg(key)}/runs`, {
        body,
        idempotent: true,
        idempotencyKey: opts.idempotencyKey,
        timeoutMs: longTimeoutMs(),
      }),
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
    /** Slow (LLM-judged assertions): long timeout; pass one Idempotency-Key per user action. */
    evaluate: (id: string, body: EvaluationRequest, opts: ActionOptions = {}) =>
      apiRequest<EvaluationReport>(cfg, 'POST', `${run(id)}/evaluations`, {
        body,
        idempotent: true,
        idempotencyKey: opts.idempotencyKey,
        timeoutMs: longTimeoutMs(),
      }),

    // ---- connect & status (UX-F3)
    /** Server mode, LLM provider, served interfaces and IMAP/SMTP listener status. */
    systemStatus: () => get<SystemStatus>('/v1/system/status'),
    /** How an agent connects to a run (endpoints, mailbox, key ids; never secrets). */
    runConnection: (id: string) => get<RunConnection>(`${run(id)}/connection`),
    /** Real call through one interface with the given agent key, made by the server. */
    testConnection: (id: string, body: ConnectionTestRequest) =>
      post<ConnectionTestResult>(`${run(id)}/connection-tests`, body),
    /** Mints a new agent key (shown once). Earlier agent keys stay valid until revoked explicitly. */
    mintAgentKey: (id: string) => post<AgentCredential>(`${run(id)}/agent-credentials`),
    revokeKey: (keyId: string) => apiRequest<void>(cfg, 'DELETE', `/v1/keys/${seg(keyId)}`),
    // ---- builder (UX-F1): scenario authoring (namespaced to avoid merge collisions)
    builder: {
      meta: () => get<AuthoringMeta>('/v1/meta/authoring'),
      validate: (body: ScenarioDraft, signal?: AbortSignal) =>
        apiRequest<DraftValidation>(cfg, 'POST', '/v1/scenario-drafts/validate', { body, signal }),
      composeEml: (body: ComposeMessage) => post<ComposedFixture>('/v1/scenario-drafts/compose-eml', body),
      inspectEml: (contentB64: string) => post<EmlSummary>('/v1/scenario-drafts/inspect-eml', { content_b64: contentB64 }),
      yamlToJson: (source: string) => post<ParsedDocument>('/v1/tools/yaml-to-json', { source }),
      jsonToYaml: (document: Record<string, unknown>) => post<RenderedYaml>('/v1/tools/json-to-yaml', { document }),
      versionDocument: (key: string, version: number) =>
        get<ScenarioVersionDocument>(`/v1/scenarios/${seg(key)}/versions/${version}/document`),
      publish: (body: ScenarioCreate) => post<ScenarioVersionOut>('/v1/scenarios', body, true),
    },
    // ---- end builder (UX-F1)
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
    /**
     * Operator download of one attachment (controller or evaluator; not an agent action). The
     * bytes are only ever handed to the browser as a download, never rendered.
     */
    attachment: (id: string, mailboxId: string, messageId: string, attachmentId: string) =>
      apiBlob(cfg, `${run(id)}/mailboxes/${seg(mailboxId)}/messages/${seg(messageId)}/attachments/${seg(attachmentId)}`),
    /**
     * Download a file URL the server handed out (ArtifactOut.url). It must resolve, on the API's
     * own origin, to a path under `<API base path>/v1/` (after URL normalisation; encoded dot
     * segments refused), so a listed URL can never send the key anywhere else.
     */
    download: (path: string) => {
      try {
        return apiBlob(cfg, apiRelativePath(cfg.baseUrl, path));
      } catch (e) {
        return Promise.reject(e);
      }
    },
    activity: (id: string, afterId: number, limit = 100) =>
      get<ActivityPage>(`${run(id)}/activity`, { after_id: afterId, limit }),
    // ---- end answer keys & mailbox browser (UX-F2)
  };
}

export type Api = ReturnType<typeof createApi>;
