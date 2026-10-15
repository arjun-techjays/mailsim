// Getting-started progress, derived from what the API already knows (no extra state).
import type { RunOut, ScenarioOut } from '../api/types';

export type StepState = 'done' | 'todo' | 'unknown';

export interface Facts {
  scenarios?: ScenarioOut[];
  /** Number of answer keys found for the scenarios checked (evaluator roles only). */
  answerKeys?: number;
  /** Newest first; undefined when the run list is not available. */
  runs?: RunOut[];
  /** Whether the newest run's clock has been advanced (clock.advanced events). */
  clockAdvanced?: boolean;
}

const EVALUATED = ['PASSED', 'FAILED', 'INCONCLUSIVE'];

export function isEvaluated(run: RunOut): boolean {
  return EVALUATED.includes(run.status);
}

const known = (v: boolean | undefined): StepState => (v === undefined ? 'unknown' : v ? 'done' : 'todo');

export interface Progress {
  scenario: StepState;
  answerKey: StepState;
  run: StepState;
  connect: StepState;
  advance: StepState;
  evaluate: StepState;
  compare: StepState;
}

export function deriveProgress(f: Facts): Progress {
  const runs = f.runs;
  const evaluated = runs?.filter(isEvaluated);
  const builds = new Set(evaluated?.map((r) => r.agent_build).filter((b): b is string => !!b));
  return {
    scenario: known(f.scenarios && f.scenarios.length > 0),
    answerKey: known(f.answerKeys === undefined ? undefined : f.answerKeys > 0),
    run: known(runs && runs.length > 0),
    connect: known(runs && runs.some((r) => r.agent_actions > 0)),
    advance: known(f.clockAdvanced),
    evaluate: known(evaluated && evaluated.length > 0),
    compare: known(evaluated && builds.size >= 2),
  };
}
