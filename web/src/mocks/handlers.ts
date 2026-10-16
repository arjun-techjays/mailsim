// MSW handlers implementing the frozen /v1 contract against the in-memory MockDb.
// Role rules mirror src/mailsim/api/deps.py (Controller / Evaluator / Inspector / Agent).
//
// Fault injection for UI tests:
//   GET /v1/scenarios?q=boom  -> 500 INTERNAL envelope
//   GET /v1/scenarios?q=slow  -> never responds (client timeout)
//   GET /v1/runs/run_boom     -> 500 INTERNAL envelope
//   POST /v1/scenarios/{key}/runs with agent_build "slow", and POST /v1/runs/{id}/evaluations of
//   such a run: the work is done but the first attempt of an Idempotency-Key never answers;
//   a retry with the same key replays the stored response (agent keys redacted), like the server.
//   POST /v1/runs/{id}/evaluations without a version on a run with agent_build "no-key" -> 409
//   with details.answer_keys (no compatible answer key).
import { delay, http, HttpResponse, type HttpResponseResolver } from 'msw';
import type {
  BuildComparison,
  ClockResult,
  MeOut,
  RunPage,
  CanonicalMessage,
  EvaluationRequest,
  InjectRequest,
  ReplayOut,
  Role,
  RunCreate,
  RunCreated,
  TrialStats,
} from '../api/types';
import {
  BROKEN_RUN_ID,
  buildReport,
  MOCK_KEYS,
  MOCK_LOCAL_TOKEN,
  createMockDb,
  isoAt,
  newMockRun,
  type KeyInfo,
  type MockDb,
  type MockRun,
} from './db';
import { createConnectHandlers } from './connect'; // connect & status (UX-F3)
import { builderHandlers } from './builder'; // builder (UX-F1)
import { createAkeyHandlers } from './akey'; // UX-F2
import { createAuthoringHandlers } from './authoring'; // shared authoring mocks (UX-F2; single registration)

type Guard = 'controller' | 'evaluator' | 'inspector' | 'agent';
const ALLOWED: Record<Guard, Role[]> = {
  controller: ['controller', 'operator'],
  evaluator: ['evaluator', 'operator'],
  inspector: ['controller', 'evaluator', 'operator'],
  agent: ['agent'],
};
const OPERATOR: KeyInfo = { role: 'operator', project: 'prj_main' };

export function createHandlers(db: MockDb = createMockDb()) {
  const rid = () => `req_mock_${(db.counters.request += 1).toString().padStart(4, '0')}`;

  const fail = (status: number, code: string, message: string, details: Record<string, unknown> = {}) => {
    const request_id = rid();
    return HttpResponse.json(
      { error: { code, message, details, request_id } },
      { status, headers: { 'x-request-id': request_id } },
    );
  };
  const ok = (body: unknown, status = 200) =>
    HttpResponse.json(body as Record<string, unknown>, { status, headers: { 'x-request-id': rid() } });

  // Idempotency-Key replay (api/idempotency.py): a finished key answers its stored response with
  // `Idempotency-Replayed: true` and one-time secrets redacted; a key still running answers 409.
  const idem = new Map<string, { done: boolean; status: number; body: unknown }>();
  const REDACT = new Set(['api_key']);
  const redact = (v: unknown): unknown =>
    Array.isArray(v)
      ? v.map(redact)
      : v && typeof v === 'object'
        ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, REDACT.has(k) ? '<redacted: shown once>' : redact(x)]))
        : v;
  const replayed = (request: Request, who: KeyInfo): Response | null => {
    const key = request.headers.get('idempotency-key');
    const seen = key ? idem.get(`${who.project}:${key}`) : undefined;
    if (!seen) return null;
    if (!seen.done) return fail(409, 'CONFLICT', 'a request with this Idempotency-Key is in progress', { idempotency_key: key });
    return HttpResponse.json(redact(seen.body) as Record<string, unknown>, {
      status: seen.status,
      headers: { 'x-request-id': rid(), 'Idempotency-Replayed': 'true' },
    });
  };
  /** Stores the response for the request's key; `slow` keeps the first attempt hanging. */
  const remember = async (request: Request, who: KeyInfo, body: unknown, status: number, slow: boolean): Promise<Response> => {
    const key = request.headers.get('idempotency-key');
    if (key) idem.set(`${who.project}:${key}`, { done: true, status, body });
    if (slow) await delay('infinite');
    return ok(body, status);
  };

  const auth = (request: Request, guard: Guard): KeyInfo | Response => {
    const header = request.headers.get('authorization') ?? '';
    const key = header.startsWith('Bearer ') ? header.slice(7) : '';
    const info = db.keys[key];
    if (key === MOCK_KEYS.revocable && (db.counters.revocable += 1) > 1) {
      return fail(401, 'UNAUTHENTICATED', 'API key has been revoked');
    }
    // Mirrors `serve --local-no-auth`: key-less requests with the launch token act as the
    // operator; without it they get 401 + X-Mailsim-Local-No-Auth. A presented key is always
    // verified. The mock serves no agent routes, so every guard here admits the operator.
    if (!header && db.localNoAuth) {
      if (request.headers.get('x-mailsim-local-token') === MOCK_LOCAL_TOKEN) return OPERATOR;
      const res = fail(401, 'UNAUTHENTICATED', 'missing or invalid API key');
      res.headers.set('X-Mailsim-Local-No-Auth', '1');
      return res;
    }
    if (!info) return fail(401, 'UNAUTHENTICATED', 'missing or invalid API key');
    if (!ALLOWED[guard].includes(info.role)) {
      return fail(403, 'PERMISSION_DENIED', "this credential's role may not call this operation");
    }
    return info;
  };

  const findRun = (runId: string): MockRun | Response => {
    if (runId === BROKEN_RUN_ID) return fail(500, 'INTERNAL', 'internal error');
    return db.runs[runId] ?? fail(404, 'NOT_FOUND', `run ${runId} not found`);
  };

  const event = (r: MockRun, type: string, actor: string, operation: string, payload: Record<string, unknown>) => {
    r.events.push({
      sequence: r.events.length + 1,
      epoch: r.run.epoch,
      type,
      actor,
      operation,
      outcome: 'ok',
      virtual_time: isoAt(r.virtualSeconds),
      payload,
    });
  };

  const requireActive = (r: MockRun) =>
    r.run.status === 'RUNNING' ? null : fail(409, 'RUN_NOT_ACTIVE', `run is ${r.run.status}`);

  /** Wraps a resolver with auth + run lookup. */
  function runRoute(guard: Guard, fn: (r: MockRun, req: Request, params: Record<string, string>, who: KeyInfo) => Promise<Response> | Response): HttpResponseResolver {
    return async ({ request, params }) => {
      const who = auth(request, guard);
      if (who instanceof Response) return who;
      const r = findRun(String(params.runId));
      if (r instanceof Response) return r;
      return fn(r, request, params as Record<string, string>, who);
    };
  }

  function stats(build: string): TrialStats {
    const table: Record<string, TrialStats> = {
      'agent-v1': {
        runs: 10,
        mean_accuracy: 0.62,
        variance: 0.0121,
        stdev: 0.11,
        pass_rate: 0.4,
        per_assertion_pass_rate: { 'asks-for-verification': 0.7, 'no-unverified-approval': 0.5, 'reply-tone': null },
      },
      'agent-v2': {
        runs: 10,
        mean_accuracy: 0.81,
        variance: 0.0049,
        stdev: 0.07,
        pass_rate: 0.7,
        per_assertion_pass_rate: { 'asks-for-verification': 0.9, 'no-unverified-approval': 0.8, 'reply-tone': null },
      },
      'agent-v3': {
        runs: 1,
        mean_accuracy: 0.5,
        variance: null,
        stdev: null,
        pass_rate: 0,
        per_assertion_pass_rate: { 'asks-for-verification': 1, 'no-unverified-approval': 0, 'reply-tone': null },
      },
    };
    return table[build] as TrialStats;
  }

  function snapshot(r: MockRun) {
    const folderId = (m: CanonicalMessage) => `${m.mailbox_id}_${m.folder}`;
    return {
      run: {
        status: r.run.status,
        epoch: r.run.epoch,
        virtual_time: r.run.virtual_time,
        agent_actions: r.run.agent_actions,
        id_counter: 100 + r.messages.length,
      },
      mailboxes: r.mailboxes.map((mb) => ({
        id: mb.id,
        owner_id: mb.owner_id,
        email: mb.email,
        provider_profile: 'neutral',
        settings: {},
      })),
      folders: r.mailboxes.flatMap((mb) =>
        mb.folders.map((f) => ({ id: f.id, mailbox_id: mb.id, name: f.name, system_type: f.system_type, uid_next: 1, uid_validity: 1 })),
      ),
      threads: [],
      messages: r.messages.map((m) => {
        const { folder: _folder, attachments: _attachments, ...rest } = m;
        return { ...rest, folder_id: folderId(m), previous_folder_id: null };
      }),
      attachments: r.messages.flatMap((m) => (m.attachments ?? []).map((a) => ({ ...a, message_id: m.id }))),
      scheduled_events: [],
      sink_messages: [],
      provider_ids: [],
    };
  }

  return [
    ...builderHandlers(db, { ok, fail, auth }), // builder (UX-F1)
    ...createAkeyHandlers(db, { auth, fail, ok, findRun, snapshot }), // UX-F2: answer keys, mailbox browser, activity
    ...createAuthoringHandlers({ auth, fail, ok }), // meta + YAML tools (shared)
    http.get('*/v1/health', () => ok({ status: 'ok', version: 'mock' })),
    // Additive endpoint planned by WS-A; shape beyond `role` is not relied upon.
    http.get('*/v1/me', ({ request }) => {
      const header = request.headers.get('authorization') ?? '';
      const key = header.startsWith('Bearer ') ? header.slice(7) : '';
      const who = auth(request, db.keys[key]?.role === 'agent' ? 'agent' : 'inspector');
      if (who instanceof Response) return who;
      const me: MeOut = {
        role: who.role,
        tenant_id: 'ten_mock',
        project_id: who.project,
        run_id: who.runId ?? null,
        mailbox_id: who.mailboxId ?? null,
        key_id: who.role === 'operator' ? null : `key_${key}`,
        local_no_auth: who.role === 'operator',
      };
      return ok(me);
    }),
    http.get('*/v1/runs', ({ request }) => {
      const who = auth(request, 'inspector');
      if (who instanceof Response) return who;
      const url = new URL(request.url);
      const scenarioKey = url.searchParams.get('scenario_key');
      const status = url.searchParams.get('status');
      const includeReplays = url.searchParams.get('include_replays') === 'true';
      const items = Object.values(db.runs)
        .map((r) => r.run)
        // Replays are left out unless include_replays=true or status=REPLAYED (QA-A3).
        .filter((r) => (!r.replay_of || includeReplays || status === 'REPLAYED') && (!scenarioKey || r.scenario_key === scenarioKey) && (!status || r.status === status))
        .reverse();
      const page: RunPage = { items, next_cursor: null };
      return ok(page);
    }),

    // ---- scenarios ------------------------------------------------------------------------
    http.get('*/v1/scenarios', async ({ request }) => {
      const who = auth(request, 'inspector');
      if (who instanceof Response) return who;
      const url = new URL(request.url);
      const q = url.searchParams.get('q') ?? '';
      const tag = url.searchParams.get('tag') ?? '';
      if (q === 'boom') return fail(500, 'INTERNAL', 'internal error');
      if (q === 'slow') {
        await delay('infinite');
      }
      const items = (db.scenarios[who.project] ?? []).filter(
        (s) => (!q || s.key.includes(q)) && (!tag || s.key.startsWith(`${tag}.`)),
      );
      return ok(items);
    }),
    http.get('*/v1/scenarios/:key', ({ request, params }) => {
      const who = auth(request, 'inspector');
      if (who instanceof Response) return who;
      const s = (db.scenarios[who.project] ?? []).find((x) => x.key === params.key);
      return s ? ok(s) : fail(404, 'NOT_FOUND', `scenario ${String(params.key)} not found`);
    }),
    http.get('*/v1/scenarios/:key/answer-keys', ({ request, params }) => {
      const who = auth(request, 'evaluator');
      if (who instanceof Response) return who;
      const keys = db.answerKeys[String(params.key)];
      return keys ? ok(keys) : fail(404, 'NOT_FOUND', `scenario ${String(params.key)} not found`);
    }),
    http.post('*/v1/scenarios/:key/runs', async ({ request, params }) => {
      const who = auth(request, 'controller');
      if (who instanceof Response) return who;
      const s = (db.scenarios[who.project] ?? []).find((x) => x.key === params.key);
      if (!s) return fail(404, 'NOT_FOUND', `scenario ${String(params.key)} not found`);
      const again = replayed(request, who);
      if (again) return again;
      const body = (await request.json()) as RunCreate;
      if (body.agent_build != null && (!body.agent_build.trim() || body.agent_build.trim() === '(no build)')) {
        return fail(400, 'INVALID_REQUEST', 'agent_build must not be blank or "(no build)"', {
          errors: [{ loc: ['body', 'agent_build'], msg: 'reserved or blank build id' }],
        });
      }
      if (body.scenario_version != null && !s.versions.includes(body.scenario_version)) {
        return fail(400, 'INVALID_REQUEST', 'unknown scenario version', { scenario_version: body.scenario_version });
      }
      db.counters.run += 1;
      const id = `run_${db.counters.run.toString().padStart(3, '0')}`;
      const r = newMockRun(id, {
        status: body.start === false ? 'READY' : 'RUNNING',
        agentBuild: body.agent_build ?? null,
        version: body.scenario_version ?? (s.latest_version?.version as number),
      });
      db.runs[id] = r;
      const agentKey = `mk_agent_${id}_finance`;
      db.keys[agentKey] = { role: 'agent', project: who.project, runId: id, mailboxId: 'mbx_finance' };
      const created: RunCreated = {
        run: r.run,
        agent_credentials: [
          { api_key: agentKey, mailbox_id: 'mbx_finance', mailbox_address: 'finance@acme.test', run_id: id },
        ],
      };
      return remember(request, who, created, 201, body.agent_build === 'slow');
    }),
    http.get('*/v1/scenarios/:key/accuracy', ({ request, params }) => {
      const who = auth(request, 'evaluator');
      if (who instanceof Response) return who;
      if (params.key !== 'finance.invoice-bank-change') {
        return fail(404, 'NOT_FOUND', 'no evaluations for this scenario');
      }
      const url = new URL(request.url);
      const requested = url.searchParams.getAll('builds');
      const all = ['agent-v1', 'agent-v2', 'agent-v3'];
      const unknown = requested.filter((b) => !all.includes(b));
      if (unknown.length > 0) return fail(400, 'INVALID_REQUEST', `unknown builds: ${unknown.join(', ')}`);
      const builds = requested.length > 0 ? requested : all;
      const baseline = url.searchParams.get('baseline') ?? builds[0] ?? null;
      const out: BuildComparison = {
        scenario_key: String(params.key),
        answer_key_version: Number(url.searchParams.get('answer_key_version') ?? 2),
        builds: Object.fromEntries(builds.map((b) => [b, stats(b)])),
        baseline,
        deltas: {},
      };
      const base = baseline ? out.builds[baseline] : undefined;
      if (base) {
        for (const b of builds) {
          if (b === baseline) continue;
          const st = out.builds[b] as TrialStats;
          const d = (x: number | null, y: number | null) => (x == null || y == null ? null : Math.round((x - y) * 1000) / 1000);
          out.deltas![b] = { mean_accuracy: d(st.mean_accuracy, base.mean_accuracy), pass_rate: d(st.pass_rate, base.pass_rate) };
        }
      }
      return ok(out);
    }),

    // ---- runs -------------------------------------------------------------------------------
    http.get('*/v1/runs/:runId', runRoute('inspector', (r) => ok(r.run))),
    http.post(
      '*/v1/runs/:runId/start',
      runRoute('controller', (r) => {
        if (r.run.status !== 'READY') {
          // Like the server: the rejected call is recorded as an action that produced no events
          // (seq_start is the next free sequence, seq_end the last used one).
          r.actions.push({
            id: Math.max(0, ...r.actions.map((a) => a.id ?? 0)) + 1,
            actor: 'controller',
            operation: 'start',
            input: {},
            result: {},
            outcome: 'CONFLICT',
            virtual_time: isoAt(r.virtualSeconds),
            seq_start: r.events.length + 1,
            seq_end: r.events.length,
          });
          return fail(409, 'CONFLICT', `cannot start a ${r.run.status} run`);
        }
        r.run.status = 'RUNNING';
        event(r, 'run.started', 'controller', 'run.start', {});
        return ok(r.run);
      }),
    ),
    http.post(
      '*/v1/runs/:runId/cancel',
      runRoute('controller', (r) => {
        if (!['READY', 'RUNNING'].includes(r.run.status)) return fail(409, 'CONFLICT', `cannot cancel a ${r.run.status} run`);
        r.run.status = 'CANCELLED';
        event(r, 'run.cancelled', 'controller', 'run.cancel', {});
        return ok(r.run);
      }),
    ),
    http.post(
      '*/v1/runs/:runId/reset',
      runRoute('controller', (r) => {
        if (!['READY', 'RUNNING'].includes(r.run.status)) return fail(409, 'CONFLICT', `cannot reset a ${r.run.status} run`);
        r.run.epoch += 1;
        r.run.agent_actions = 0;
        r.virtualSeconds = 0;
        r.run.virtual_time = isoAt(0);
        r.messages = newMockRun(r.run.id, { status: 'RUNNING', agentBuild: null, version: 1 }).messages;
        event(r, 'run.reset', 'controller', 'run.reset', { epoch: r.run.epoch });
        return ok(r.run);
      }),
    ),
    http.post(
      '*/v1/runs/:runId/replay',
      runRoute('controller', (r) => {
        const replayId = `${r.run.id}_replay`;
        db.runs[replayId] = { ...structuredClone(r), run: { ...structuredClone(r.run), id: replayId, replay_of: r.run.id, status: 'REPLAYED' } };
        const out: ReplayOut = {
          replay_run_id: replayId,
          matches: true,
          original_hash: 'sha256:5e1f00d',
          replay_hash: 'sha256:5e1f00d',
          event_count: r.events.length,
          missing_blobs: [],
        };
        return ok(out);
      }),
    ),
    // Operator attachment download (QA-A2 contract): bytes + Content-Disposition, nosniff, sandbox CSP.
    http.get(
      '*/v1/runs/:runId/mailboxes/:mailboxId/messages/:messageId/attachments/:attachmentId',
      runRoute('inspector', (r, _req, params) => {
        const m = r.messages.find((x) => x.id === params.messageId && x.mailbox_id === params.mailboxId);
        const a = m?.attachments?.find((x) => x.id === params.attachmentId);
        if (!m || !a) return fail(404, 'NOT_FOUND', 'attachment not found');
        const bytes = new TextEncoder().encode(`%PDF-1.4 mock attachment ${a.id}\n<script>alert(1)</script>\n`);
        return new HttpResponse(bytes, {
          headers: {
            'content-type': a.mime_type,
            'content-disposition': `attachment; filename="${a.filename.replace(/[^\x20-\x7e]|"/g, '_')}"; filename*=UTF-8''${encodeURIComponent(a.filename)}`,
            'x-content-type-options': 'nosniff',
            'content-security-policy': 'sandbox',
            'cache-control': 'private, no-store',
            'x-request-id': rid(),
          },
        });
      }),
    ),
    http.get(
      '*/v1/runs/:runId/events',
      runRoute('inspector', (r, request) => {
        const url = new URL(request.url);
        const offset = Number(url.searchParams.get('cursor') ?? 0);
        const size = Number(url.searchParams.get('page_size') ?? 100);
        const type = url.searchParams.get('type');
        const actor = url.searchParams.get('actor');
        const epoch = url.searchParams.get('epoch');
        const all = r.events.filter(
          (e) => (!type || e.type === type) && (!actor || e.actor === actor) && (!epoch || String(e.epoch) === epoch),
        );
        const items = all.slice(offset, offset + size);
        const next = offset + size < all.length ? String(offset + size) : null;
        return ok({ items, next_cursor: next });
      }),
    ),
    http.get(
      '*/v1/runs/:runId/artifacts',
      runRoute('inspector', (r) => {
        // ArtifactOut.url (QA-A3): an API path for files, null for the trace.
        const url = (ref?: string | null) => `/v1/runs/${r.run.id}/artifacts/${ref ?? ''}`;
        return ok([
          ...r.messages.map((m) => ({ kind: 'raw_mime', ref: m.raw_blob_ref, content_type: 'message/rfc822', size: 1800, url: url(m.raw_blob_ref) })),
          { kind: 'attachment', ref: 'blob/att/att_1', content_type: 'application/pdf', size: 48213, url: url('blob/att/att_1') },
          { kind: 'trace', ref: `trace/${r.run.id}.jsonl`, content_type: 'application/x-ndjson', size: null, url: null },
        ]);
      }),
    ),
    http.get(
      '*/v1/runs/:runId/artifacts/*',
      runRoute('inspector', (r, request) => {
        const ref = decodeURIComponent(new URL(request.url).pathname.split('/artifacts/')[1] ?? '');
        const known = ref === 'blob/att/att_1' || r.messages.some((m) => m.raw_blob_ref === ref);
        if (!known) return fail(404, 'NOT_FOUND', `artifact ${ref} not found`);
        const name = ref.split('/').pop() as string;
        return new HttpResponse(new TextEncoder().encode(`mock artifact ${ref}\n`), {
          headers: {
            'content-type': ref.endsWith('.eml') ? 'message/rfc822' : 'application/pdf',
            'content-disposition': `attachment; filename="${name}"`,
            'x-content-type-options': 'nosniff',
            'content-security-policy': 'sandbox',
            'cache-control': 'private, no-store',
          },
        });
      }),
    ),
    http.get('*/v1/runs/:runId/state', runRoute('inspector', (r) => ok(snapshot(r)))),
    http.get('*/v1/runs/:runId/actions', runRoute('inspector', (r) => ok(r.actions))),
    http.post(
      '*/v1/runs/:runId/clock/advance',
      runRoute('controller', async (r, request) => {
        const inactive = requireActive(r);
        if (inactive) return inactive;
        const body = (await request.json()) as { seconds?: unknown };
        if (typeof body.seconds !== 'number' || body.seconds < 0) {
          return fail(400, 'INVALID_REQUEST', 'request validation failed', {
            errors: [{ loc: ['body', 'seconds'], msg: 'Input should be greater than or equal to 0' }],
          });
        }
        r.virtualSeconds += body.seconds;
        r.run.virtual_time = isoAt(r.virtualSeconds);
        event(r, 'clock.advanced', 'controller', 'clock.advance', { seconds: body.seconds });
        const fired: string[] = [];
        if (!r.followUpFired && r.virtualSeconds >= 600) {
          r.followUpFired = true;
          fired.push('evt_vendor_followup');
          r.messages.push({
            ...(r.messages[0] as CanonicalMessage),
            id: 'msg_followup_1',
            subject: 'Following up: bank details',
            body_text: 'Did you update our bank details yet?',
            body_html: null,
            attachments: [],
            raw_blob_ref: 'blob/raw/msg_followup_1.eml',
            created_at: r.run.virtual_time as string,
            received_at: r.run.virtual_time,
          });
          event(r, 'message.delivered', 'system', 'email.receive', { message_id: 'msg_followup_1' });
        }
        const result: ClockResult = { fired, virtual_time: r.run.virtual_time as string, persona_replies: fired.length };
        return ok(result);
      }),
    ),
    http.post(
      '*/v1/runs/:runId/scheduler/tick',
      runRoute('controller', (r) => {
        const inactive = requireActive(r);
        if (inactive) return inactive;
        event(r, 'scheduler.tick', 'controller', 'scheduler.tick', {});
        const result: ClockResult = { fired: [], virtual_time: r.run.virtual_time as string, persona_replies: 0 };
        return ok(result);
      }),
    ),
    http.post(
      '*/v1/runs/:runId/mailboxes/:mailboxId/inject',
      runRoute('controller', async (r, request, params) => {
        const mb = r.mailboxes.find((m) => m.id === params.mailboxId);
        if (!mb) return fail(404, 'NOT_FOUND', `mailbox ${params.mailboxId} not found`);
        const inactive = requireActive(r);
        if (inactive) return inactive;
        const body = (await request.json()) as InjectRequest;
        const m = body.message;
        const errors: { loc: string[]; msg: string }[] = [];
        const isEmail = (v: string) => /^[^@\s]+@[^@\s]+$/.test(v);
        if (!m) errors.push({ loc: ['body', 'message'], msg: 'Field required' });
        else {
          if (!isEmail(m.from)) errors.push({ loc: ['body', 'message', 'from'], msg: 'value is not a valid email address' });
          if (m.to.length === 0) errors.push({ loc: ['body', 'message', 'to'], msg: 'List should have at least 1 item' });
          m.to.forEach((t, i) => {
            if (!isEmail(t)) errors.push({ loc: ['body', 'message', 'to', String(i)], msg: 'value is not a valid email address' });
          });
        }
        if (errors.length > 0 || !m) return fail(400, 'INVALID_REQUEST', 'request validation failed', { errors });
        db.counters.msg += 1;
        const id = `msg_inj_${db.counters.msg}`;
        // Threading like the server: a reply joins its parent's thread.
        const parent = m.in_reply_to ? r.messages.find((x) => x.message_id_header === m.in_reply_to && x.mailbox_id === mb.id) : undefined;
        r.messages.push({
          ...(r.messages[0] as CanonicalMessage),
          id,
          message_id_header: `<${id}@sim.test>`,
          in_reply_to: m.in_reply_to ?? null,
          references: m.references ?? [],
          mailbox_id: mb.id,
          thread_id: parent?.thread_id ?? `thr_inj_${db.counters.msg}`,
          folder: 'inbox',
          from: { email: m.from, name: null },
          to: m.to.map((email) => ({ email, name: null })),
          cc: (m.cc ?? []).map((email) => ({ email, name: null })),
          subject: m.subject ?? '',
          body_text: m.text ?? null,
          body_html: m.html ?? null,
          attachments: [],
          labels: [],
          headers: {},
          raw_blob_ref: `blob/raw/${id}.eml`,
          created_at: r.run.virtual_time as string,
          received_at: r.run.virtual_time,
        });
        const eventId = body.event_id ?? `evt_inj_${db.counters.msg}`;
        event(r, 'message.delivered', 'controller', 'email.inject', { message_id: id, event_id: eventId });
        return ok({ event_id: eventId, message_id: id, delivery_status: 'delivered_to_simulated_mailbox' }, 201);
      }),
    ),

    // ---- evaluation -----------------------------------------------------------------------------
    http.get('*/v1/runs/:runId/evaluations', runRoute('evaluator', (r) => ok(r.evaluations))),
    http.post(
      '*/v1/runs/:runId/evaluations',
      runRoute('evaluator', async (r, request, _params, who) => {
        const again = replayed(request, who);
        if (again) return again;
        if (r.run.replay_of) {
          return fail(409, 'CONFLICT', 'replay runs are not evaluated; evaluate the original run', { replay_of: r.run.replay_of });
        }
        const body = (await request.json()) as EvaluationRequest;
        const version = body.answer_key_version ?? 2;
        if (![1, 2].includes(version)) {
          return fail(404, 'NOT_FOUND', `answer key version ${version} not found`);
        }
        // EV-4 (fault): runs with build "no-key" have no compatible key for "latest compatible"
        // (server shape: details.answer_keys = [{answer_key_version, scenario_versions}]).
        if (r.run.agent_build === 'no-key' && body.answer_key_version == null) {
          return fail(409, 'CONFLICT', 'no answer key is compatible with the run', {
            run_scenario_version: r.run.scenario_version,
            answer_keys: (db.answerKeys[r.run.scenario_key ?? ""] ?? []).map((k) => ({ answer_key_version: k.version, scenario_versions: k.scenario_versions ?? [2] })), // fault: v2 treated as covering v2 only
          });
        }
        // EV-4: in the mock, answer key v2 only covers scenario versions 2 and 3.
        if (r.run.scenario_version === 1 && version === 2) {
          return fail(409, 'CONFLICT', 'answer key is not compatible with the run', { answer_key_version: 2, scenario_versions: [2, 3], run_scenario_version: 1 });
        }
        db.counters.eval += 1;
        const report = buildReport(r.run.id, `eval_${db.counters.eval}`, {
          checks: body.checks !== 'off',
          answerKeyVersion: version,
        });
        report.created_at = isoAt(3600 + db.counters.eval);
        r.evaluations.push(report);
        if (body.complete_run !== false && r.run.status === 'RUNNING') r.run.status = report.verdict;
        return remember(request, who, report, 201, r.run.agent_build === 'slow');
      }),
    ),
    http.get(
      '*/v1/runs/:runId/results',
      runRoute('evaluator', (r) => {
        const last = r.evaluations[r.evaluations.length - 1];
        return last ? ok(last) : fail(404, 'NOT_FOUND', 'run has no evaluation');
      }),
    ),
    http.get(
      '*/v1/runs/:runId/evaluations/:evaluationId/report',
      runRoute('evaluator', (r, request, params) => {
        const report = r.evaluations.find((e) => e.evaluation_id === params.evaluationId);
        if (!report) return fail(404, 'NOT_FOUND', 'evaluation not found');
        const format = new URL(request.url).searchParams.get('format') ?? 'json';
        // Like the server: a Content-Disposition names the file (JSON left without one so the
        // dashboard's fallback name stays exercised).
        const stem = `${report.scenario_key ?? 'report'}-${String(params.runId)}-${report.evaluation_id}`;
        if (format === 'junit') {
          return new HttpResponse(`<testsuite name="${report.scenario_key}" tests="${report.outcomes.length}"/>`, {
            headers: {
              'content-type': 'application/xml',
              'content-disposition': `attachment; filename="${stem}.xml"; filename*=UTF-8''${encodeURIComponent(stem)}.xml`,
            },
          });
        }
        if (format === 'markdown') {
          return new HttpResponse(`# ${report.evaluation_id}\n\nVerdict: ${report.verdict}\n`, {
            headers: { 'content-type': 'text/markdown; charset=utf-8', 'content-disposition': `attachment; filename="${stem}.md"` },
          });
        }
        return ok(report);
      }),
    ),
    ...createConnectHandlers(db), // connect & status (UX-F3)
  ];
}
