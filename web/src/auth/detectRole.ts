import { isApiError, type ApiConfig } from '../api/client';
import { createApi } from '../api/endpoints';
import type { Role } from '../api/types';

/** Scenario key that cannot exist; used only to learn whether answer keys are readable. */
export const ROLE_PROBE_KEY = '__mailsim_role_probe__';

/**
 * The v1 contract has no "who am I" endpoint, so the role is inferred from the role guards:
 *  1. GET /v1/scenarios (controller|evaluator). 403 => agent key.
 *  2. GET /v1/scenarios/{probe}/answer-keys (evaluator only). 403 => controller;
 *     200/404 => evaluator (the role guard runs before the scenario lookup).
 * 401 and any other failure propagate to the caller.
 */
export async function detectRole(cfg: ApiConfig): Promise<Role> {
  const api = createApi(cfg);
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
