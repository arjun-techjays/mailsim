// MSW handlers for the "connect your agent" + system status contract (UX-F3):
//   GET  /v1/system/status, GET /v1/runs/{id}/connection, POST /v1/runs/{id}/connection-tests,
//   POST /v1/runs/{id}/agent-credentials, DELETE /v1/keys/{key_id}.
// Mirrors src/mailsim/api/routers/ux.py + control.py: operator-side only (controller key or the
// local no-auth operator); minting a key revokes the run's earlier agent keys.
//
// Fault injection for UI tests: `setMockListeners(db, {imap: false})` takes a listener down.
import { http, HttpResponse } from 'msw';
import type {
  AgentCredential,
  AgentInterface,
  AgentKeyInfo,
  ConnectionTestRequest,
  ConnectionTestResult,
  InterfaceEndpoint,
  RunConnection,
  SystemStatus,
} from '../api/types';
import { isoAt, MOCK_LOCAL_TOKEN, type KeyInfo, type MockDb, type MockMailbox } from './db';

export const MOCK_API_ORIGIN = 'http://127.0.0.1:8000';
export const MOCK_IMAP_PORT = 1143;
export const MOCK_SMTP_PORT = 1587;
const INTERFACES: AgentInterface[] = ['rest', 'gmail', 'graph', 'mcp_http', 'mcp_stdio', 'imap', 'smtp'];
const TERMINAL = ['PASSED', 'FAILED', 'ERROR', 'CANCELLED'];

interface KeyMeta extends AgentKeyInfo {
  secret: string;
  runId: string;
}
interface ConnState {
  meta: KeyMeta[];
  minted: number;
  listeners: { imap: boolean; smtp: boolean };
  llm: { provider: string; model: string | null };
}

const STATE = new WeakMap<MockDb, ConnState>();

/** 12-hex prefix of an msk_<prefix>_<secret> key (mock fixture keys have none). */
const prefixOf = (secret: string) => /^msk_([0-9a-f]{12})_/.exec(secret)?.[1] ?? null;

function state(db: MockDb): ConnState {
  let s = STATE.get(db);
  if (!s) {
    s = { meta: [], minted: 0, listeners: { imap: true, smtp: true }, llm: { provider: 'stub', model: null } };
    STATE.set(db, s);
  }
  // Agent keys created elsewhere (run creation, fixtures) get metadata on first sight.
  for (const [secret, info] of Object.entries(db.keys)) {
    if (info.role === 'agent' && info.runId && !s.meta.some((m) => m.secret === secret)) {
      s.meta.push({ secret, runId: info.runId, key_id: `key_${secret}`, prefix: prefixOf(secret), created_at: isoAt(s.meta.length), revoked: false });
    }
  }
  return s;
}

export function setMockListeners(db: MockDb, up: Partial<ConnState['listeners']>): void {
  Object.assign(state(db).listeners, up);
}

export function setMockLlm(db: MockDb, provider: string, model: string | null = null): void {
  state(db).llm = { provider, model };
}

export function createConnectHandlers(db: MockDb) {
  let request = 0;
  const rid = () => `req_mock_c${(request += 1).toString().padStart(4, '0')}`;
  const fail = (status: number, code: string, message: string, details: Record<string, unknown> = {}) => {
    const request_id = rid();
    return HttpResponse.json({ error: { code, message, details, request_id } }, { status, headers: { 'x-request-id': request_id } });
  };
  const ok = (body: unknown, status = 200) => HttpResponse.json(body as Record<string, unknown>, { status });

  /** Controller (or inspector) guard, mirroring handlers.ts and serve --local-no-auth. */
  const auth = (req: Request, guard: 'controller' | 'inspector'): KeyInfo | Response => {
    const header = req.headers.get('authorization') ?? '';
    const key = header.startsWith('Bearer ') ? header.slice(7) : '';
    if (!header && db.localNoAuth && req.headers.get('x-mailsim-local-token') === MOCK_LOCAL_TOKEN) {
      return { role: 'operator', project: 'prj_main' };
    }
    const info = db.keys[key];
    if (!info) return fail(401, 'UNAUTHENTICATED', 'missing or invalid API key');
    const allowed = guard === 'controller' ? ['controller', 'operator'] : ['controller', 'evaluator', 'operator'];
    if (!allowed.includes(info.role)) return fail(403, 'PERMISSION_DENIED', "this credential's role may not call this operation");
    return info;
  };

  const lookupRun = (runId: string) => db.runs[runId] ?? fail(404, 'NOT_FOUND', `run ${runId} not found`);

  const endpoints = (address: string): InterfaceEndpoint[] => {
    const { listeners } = state(db);
    return [
      { interface: 'rest', available: true, base_url: `${MOCK_API_ORIGIN}/v1` },
      { interface: 'gmail', available: true, base_url: `${MOCK_API_ORIGIN}/gmail/v1` },
      { interface: 'graph', available: true, base_url: `${MOCK_API_ORIGIN}/graph/v1.0` },
      { interface: 'mcp_http', available: true, base_url: `${MOCK_API_ORIGIN}/mcp` },
      {
        interface: 'mcp_stdio',
        available: true,
        command: ['uv', 'run', 'python', '-m', 'mailsim.serve', 'mcp-stdio'],
        note: 'Run it with the same MAILSIM_DATABASE_URL and MAILSIM_BLOB_DIR as the API server.',
      },
      { interface: 'imap', available: listeners.imap, host: '127.0.0.1', port: MOCK_IMAP_PORT, tls: 'none', username: address },
      { interface: 'smtp', available: listeners.smtp, host: '127.0.0.1', port: MOCK_SMTP_PORT, tls: 'none', username: address },
    ];
  };

  const SUCCESS: Record<AgentInterface, (address: string, mailboxId: string) => Omit<ConnectionTestResult, 'interface' | 'ok'>> = {
    rest: (_a, m) => ({ summary: `GET /v1/mailboxes/${m}/folders answered 200 (5 folders).`, status_code: 200, details: { folders: 5 } }),
    gmail: (a) => ({ summary: `GET /gmail/v1/users/me/profile answered 200 for ${a}.`, status_code: 200, details: { emailAddress: a, messagesTotal: 3 } }),
    graph: (a) => ({ summary: `GET /graph/v1.0/me answered 200 for ${a}.`, status_code: 200, details: { mail: a } }),
    mcp_http: () => ({ summary: 'initialize + tools/list over streamable HTTP: 10 agent tools.', status_code: 200, details: { tools: 10 } }),
    mcp_stdio: () => ({ summary: 'Started mcp-stdio with MAILSIM_API_KEY; tools/list returned 10 agent tools.', status_code: null, details: { tools: 10 } }),
    imap: () => ({ summary: 'LOGIN and SELECT INBOX succeeded (3 messages).', status_code: null, details: { exists: 3 } }),
    smtp: () => ({ summary: 'EHLO and AUTH PLAIN succeeded (235 2.7.0). No mail was sent.', status_code: 235, details: {} }),
  };
  /** Wire-level message the interface gave (status_code + error_message); the code is unified. */
  const DENIED: Record<AgentInterface, Pick<ConnectionTestResult, 'status_code' | 'error_message'>> = {
    rest: { status_code: 401, error_message: 'missing or invalid API key' },
    gmail: { status_code: 401, error_message: 'Request had invalid authentication credentials.' },
    graph: { status_code: 401, error_message: 'InvalidAuthenticationToken: Access token is empty or invalid.' },
    mcp_http: { status_code: 401, error_message: 'missing or invalid API key' },
    mcp_stdio: { status_code: null, error_message: 'mcp-stdio exited with status 2: missing or invalid API key' },
    imap: { status_code: null, error_message: 'NO [AUTHENTICATIONFAILED] invalid credentials' },
    smtp: { status_code: 535, error_message: '535 5.7.8 Authentication credentials invalid' },
  };

  return [
    http.get('*/v1/system/status', ({ request: req }) => {
      const who = auth(req, 'inspector');
      if (who instanceof Response) return who;
      const s = state(db);
      const status: SystemStatus = {
        version: 'mock',
        local_no_auth: who.role === 'operator',
        llm_provider: s.llm.provider,
        llm_model: s.llm.model,
        interfaces: INTERFACES,
        listeners: {
          imap: { running: s.listeners.imap, host: '127.0.0.1', port: MOCK_IMAP_PORT, tls: 'none' },
          smtp: { running: s.listeners.smtp, host: '127.0.0.1', port: MOCK_SMTP_PORT, tls: 'none' },
        },
      };
      return ok(status);
    }),

    http.get('*/v1/runs/:runId/connection', ({ request: req, params }) => {
      const who = auth(req, 'controller');
      if (who instanceof Response) return who;
      const r = lookupRun(String(params.runId));
      if (r instanceof Response) return r;
      const mbx = r.mailboxes[0] as MockMailbox;
      const keys: AgentKeyInfo[] = state(db)
        .meta.filter((m) => m.runId === r.run.id)
        .map(({ key_id, prefix, created_at, expires_at, revoked }) => ({ key_id, prefix, created_at, expires_at: expires_at ?? null, revoked }));
      const out: RunConnection = {
        run_id: r.run.id,
        run_status: r.run.status,
        mailbox_id: mbx.id,
        mailbox_address: mbx.email,
        persona_id: mbx.owner_id.replace(/^per_/, ''),
        interfaces: endpoints(mbx.email),
        agent_keys: keys,
      };
      return ok(out);
    }),

    http.post('*/v1/runs/:runId/connection-tests', async ({ request: req, params }) => {
      const who = auth(req, 'controller');
      if (who instanceof Response) return who;
      const r = lookupRun(String(params.runId));
      if (r instanceof Response) return r;
      const body = (await req.json()) as ConnectionTestRequest;
      if (!INTERFACES.includes(body.interface) || !body.api_key) { // FastAPI validation -> 400 envelope
        return fail(400, 'INVALID_REQUEST', 'request validation failed', { errors: [{ loc: ['body', 'interface'], msg: 'invalid' }] });
      }
      const iface = body.interface;
      const mbx = r.mailboxes[0] as MockMailbox;
      const base = { interface: iface, details: {} };
      if ((iface === 'imap' || iface === 'smtp') && !state(db).listeners[iface]) {
        const port = iface === 'imap' ? MOCK_IMAP_PORT : MOCK_SMTP_PORT;
        const out: ConnectionTestResult = {
          ...base,
          ok: false,
          summary: `Could not connect to 127.0.0.1:${port}.`,
          status_code: null,
          error_code: 'LISTENER_DOWN',
          error_message: `connection refused: the ${iface.toUpperCase()} listener is not running`,
        };
        return ok(out);
      }
      const key = db.keys[body.api_key];
      // Contract: invalid/revoked/expired -> UNAUTHENTICATED (reason invalid_key); a valid key
      // that is not this run's agent key -> PERMISSION_DENIED (not_agent_key | other_run).
      if (!key) {
        return ok({
          ...base,
          ok: false,
          summary: 'The agent key was rejected.',
          error_code: 'UNAUTHENTICATED',
          ...DENIED[iface],
          details: { reason: 'invalid_key' },
        } satisfies ConnectionTestResult);
      }
      if (key.role !== 'agent' || key.runId !== r.run.id) {
        const reason = key.role !== 'agent' ? 'not_agent_key' : 'other_run';
        return ok({
          ...base,
          ok: false,
          summary: reason === 'not_agent_key' ? 'This is not an agent key.' : 'This agent key belongs to another run.',
          status_code: null,
          error_code: 'PERMISSION_DENIED',
          error_message: reason === 'not_agent_key' ? `a ${key.role} key cannot act as the agent` : 'the key is bound to another run',
          details: { reason },
        } satisfies ConnectionTestResult);
      }
      if (r.run.status !== 'RUNNING') {
        return ok({
          ...base,
          ok: false,
          summary: 'The key is valid but the run is not active.',
          status_code: 409,
          error_code: 'RUN_NOT_ACTIVE',
          error_message: `run is ${r.run.status}`,
        } satisfies ConnectionTestResult);
      }
      return ok({ ...base, ok: true, error_code: null, error_message: null, ...SUCCESS[iface](mbx.email, mbx.id) } satisfies ConnectionTestResult);
    }),

    http.post('*/v1/runs/:runId/agent-credentials', ({ request: req, params }) => {
      const who = auth(req, 'controller');
      if (who instanceof Response) return who;
      const r = lookupRun(String(params.runId));
      if (r instanceof Response) return r;
      if (TERMINAL.includes(r.run.status)) {
        return fail(409, 'CONFLICT', `run is ${r.run.status}; agent credentials are only issued for live runs`, { status: r.run.status });
      }
      const s = state(db);
      s.minted += 1;
      const n = s.minted.toString().padStart(4, '0');
      const secret = `msk_00000000${n}_mockMintedAgentKeyForTests${n}abcdef`;
      const mbx = r.mailboxes[0] as MockMailbox;
      for (const m of s.meta.filter((x) => x.runId === r.run.id && !x.revoked)) {
        m.revoked = true;
        delete db.keys[m.secret];
      }
      db.keys[secret] = { role: 'agent', project: who.project, runId: r.run.id, mailboxId: mbx.id };
      s.meta.push({ secret, runId: r.run.id, key_id: `key_minted_${n}`, prefix: prefixOf(secret), created_at: isoAt(600 + s.minted), revoked: false });
      const cred: AgentCredential = { api_key: secret, mailbox_id: mbx.id, mailbox_address: mbx.email, run_id: r.run.id };
      return ok(cred, 201);
    }),

    http.delete('*/v1/keys/:keyId', ({ request: req, params }) => {
      const who = auth(req, 'controller');
      if (who instanceof Response) return who;
      const m = state(db).meta.find((x) => x.key_id === params.keyId);
      if (!m) return fail(404, 'NOT_FOUND', `key ${String(params.keyId)} not found`);
      m.revoked = true;
      delete db.keys[m.secret];
      return new HttpResponse(null, { status: 204 });
    }),
  ];
}
