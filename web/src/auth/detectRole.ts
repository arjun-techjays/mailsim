import { isApiError, type ApiConfig } from '../api/client';
import { createApi } from '../api/endpoints';
import type { Role } from '../api/types';
import { isLoopback } from './baseUrl';

/** Scenario key that cannot exist; used only to learn whether answer keys are readable. */
export const ROLE_PROBE_KEY = '__mailsim_role_probe__';
const ROLES: readonly Role[] = ['controller', 'evaluator', 'agent', 'operator'];
const ABSENT = new Set([404, 405, 501]);

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

/**
 * Local no-auth mode (`serve --local-no-auth`): `GET /v1/me` WITHOUT a key answers
 * `{role: "operator", local_no_auth: true}`. Accepted only from a loopback API origin (the
 * server refuses to run the mode on any other address). Any failure means "not local mode".
 */
export async function detectLocalNoAuth(baseUrl: string): Promise<boolean> {
  if (!isLoopback(baseUrl)) return false;
  try {
    const me = await createApi({ baseUrl, apiKey: '' }).me();
    return me?.role === 'operator' && me.local_no_auth === true;
  } catch {
    return false;
  }
}
