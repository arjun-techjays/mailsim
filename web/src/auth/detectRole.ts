import { buildUrl, defaultTimeoutMs, isApiError, LOCAL_TOKEN_HEADER, type ApiConfig } from '../api/client';
import { createApi } from '../api/endpoints';
import type { MeOut, Role } from '../api/types';
import { isLoopback } from './baseUrl';

/** Scenario key that cannot exist; used only to learn whether answer keys are readable. */
export const ROLE_PROBE_KEY = '__mailsim_role_probe__';
const ROLES: readonly Role[] = ['controller', 'evaluator', 'agent', 'operator'];
const ABSENT = new Set([404, 405, 501]);
const LOCAL_MODE_HEADER = 'X-Mailsim-Local-No-Auth';

/**
 * 1. GET /v1/me (additive endpoint). A known `role` wins; 404/405/501 means "not deployed yet".
 * 2. Fallback, inferring the role from the route guards:
 *    GET /v1/scenarios (controller|evaluator): 403 => agent key.
 *    GET /v1/scenarios/{probe}/answer-keys (evaluator only): 403 => controller;
 *    200/404 => evaluator (the role guard runs before the scenario lookup).
 * 401 and any other failure propagate to the caller.
 */
export async function detectRole(cfg: ApiConfig): Promise<Role> {
  const api = createApi(cfg);
  try {
    const me = await api.me();
    if (me && ROLES.includes(me.role)) return me.role;
  } catch (e) {
    if (!(isApiError(e) && e.status != null && ABSENT.has(e.status))) throw e;
  }
  try {
    await api.listScenarios();
  } catch (e) {
    if (isApiError(e) && e.status === 403) return 'agent';
    throw e;
  }
  try {
    await api.listAnswerKeys(ROLE_PROBE_KEY);
    return 'evaluator';
  } catch (e) {
    if (isApiError(e) && e.status === 403) return 'controller';
    if (isApiError(e) && e.status === 404) return 'evaluator';
    throw e;
  }
}

/** `operator`: the launch token works; `needs-link`: local mode but no/stale token; `off`: key mode. */
export type LocalMode = 'operator' | 'needs-link' | 'off';

/**
 * Local no-auth mode (`serve --local-no-auth`). The server prints a dashboard link carrying a
 * launch token in the URL fragment; with it, `GET /v1/me` (no key) answers
 * `{role: "operator", local_no_auth: true}`. Without it the server answers 401 with
 * `X-Mailsim-Local-No-Auth: 1`, so the Connect page can explain where the link is. Only a
 * loopback API origin is probed (the server refuses the mode anywhere else).
 */
export async function detectLocalNoAuth(baseUrl: string, localToken?: string): Promise<LocalMode> {
  if (!isLoopback(baseUrl)) return 'off';
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (localToken) headers[LOCAL_TOKEN_HEADER] = localToken;
  try {
    const res = await fetch(buildUrl(baseUrl, '/v1/me'), {
      headers,
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
      signal: AbortSignal.timeout(defaultTimeoutMs()),
    });
    if (res.ok) {
      const me = (await res.json()) as Partial<MeOut>;
      return me.role === 'operator' && me.local_no_auth === true ? 'operator' : 'off';
    }
    return res.status === 401 && res.headers.get(LOCAL_MODE_HEADER) === '1' ? 'needs-link' : 'off';
  } catch {
    return 'off';
  }
}
