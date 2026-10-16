import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { isApiError } from '../api/client';

const list = (v: unknown): string =>
  Array.isArray(v) ? v.map((x) => `v${String(x)}`).join(', ') : v == null ? 'all versions' : `v${String(v)}`;

/** One entry of details.answer_keys: {answer_key_version, scenario_versions} (server shape; `version` tolerated). */
function keyLabel(k: unknown): string {
  if (k && typeof k === 'object') {
    const o = k as { answer_key_version?: unknown; version?: unknown; scenario_versions?: unknown };
    return `v${String(o.answer_key_version ?? o.version)} (covers scenario ${list(o.scenario_versions ?? null)})`;
  }
  return `v${String(k)}`;
}

/**
 * Plain-language explanation of an evaluation the server refused with 409 (QA-A3): a replay run,
 * or an answer key that does not cover the run's scenario version (EV-4). Null for anything else.
 */
export function EvaluationConflict({ error, scenarioKey }: { error: unknown; scenarioKey: string | null }) {
  if (!isApiError(error) || error.status !== 409) return null;
  const d = error.details;
  const writeKey = scenarioKey ? (
    <Link to={`/scenarios/${encodeURIComponent(scenarioKey)}/answer-key`}>write an answer key that covers it</Link>
  ) : (
    'write an answer key that covers it'
  );
  let body: ReactNode = null;
  if (typeof d.replay_of === 'string') {
    body = (
      <>
        This run is a replay; replays are not evaluated.{' '}
        <Link to={`/runs/${encodeURIComponent(d.replay_of)}/evaluations`}>Evaluate the original run {d.replay_of}</Link>.
      </>
    );
  } else if (d.run_scenario_version != null && d.answer_key_version != null) {
    body = (
      <>
        Answer key v{String(d.answer_key_version)} covers scenario {list(d.scenario_versions)}, but this run uses scenario
        v{String(d.run_scenario_version)}. Pick another answer-key version, or {writeKey}.
      </>
    );
  } else if (d.run_scenario_version != null && Array.isArray(d.answer_keys)) {
    body = (
      <>
        No answer key covers scenario v{String(d.run_scenario_version)}, the version this run uses
        {d.answer_keys.length > 0 ? ` (existing: ${d.answer_keys.map(keyLabel).join('; ')})` : ''}. Pick a compatible
        version, or {writeKey}.
      </>
    );
  }
  if (!body) return null;
  return (
    <p className="notice" role="alert" data-testid="evaluation-conflict">
      {body}
    </p>
  );
}
