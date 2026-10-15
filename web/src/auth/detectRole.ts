import { isApiError, type ApiConfig } from '../api/client';
import { createApi } from '../api/endpoints';
import type { Role } from '../api/types';

/** Scenario key that cannot exist; used only to learn whether answer keys are readable. */
export const ROLE_PROBE_KEY = '__mailsim_role_probe__';
const ROLES: readonly Role[] = ['controller', 'evaluator', 'agent'];
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
    if (ROLES.includes(me?.role as Role)) return me.role as Role;
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
