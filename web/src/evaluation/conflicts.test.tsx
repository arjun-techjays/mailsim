import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ApiError } from '../api/client';
import { EvaluationConflict } from './conflicts';

const conflict = (details: Record<string, unknown>, status = 409) =>
  new ApiError({ kind: 'http', status, code: 'CONFLICT', message: 'x', details });
const show = (error: unknown, scenarioKey: string | null = 'sc.k') =>
  render(
    <MemoryRouter>
      <EvaluationConflict error={error} scenarioKey={scenarioKey} />
    </MemoryRouter>,
  );

describe('EvaluationConflict', () => {
  it('explains a replay run with a link to the original', () => {
    show(conflict({ replay_of: 'run_o' }));
    expect(screen.getByTestId('evaluation-conflict')).toHaveTextContent('replays are not evaluated');
    expect(screen.getByRole('link', { name: 'Evaluate the original run run_o' })).toHaveAttribute('href', '/runs/run_o/evaluations');
  });

  it('explains an incompatible answer key (EV-4, both shapes)', () => {
    const { unmount } = show(conflict({ answer_key_version: 2, scenario_versions: [2, 3], run_scenario_version: 1 }));
    expect(screen.getByTestId('evaluation-conflict')).toHaveTextContent(
      'Answer key v2 covers scenario v2, v3, but this run uses scenario v1. Pick another answer-key version, or write an answer key that covers it.',
    );
    expect(screen.getByRole('link', { name: 'write an answer key that covers it' })).toHaveAttribute('href', '/scenarios/sc.k/answer-key');
    unmount();
    const r2 = show(conflict({ answer_key_version: 2, scenario_versions: null, run_scenario_version: 1 }), null);
    expect(screen.getByTestId('evaluation-conflict')).toHaveTextContent('covers scenario all versions');
    expect(screen.queryByRole('link')).toBeNull();
    r2.unmount();
    const r3 = show(conflict({ run_scenario_version: 4, answer_keys: [{ version: 2, scenario_versions: [2] }, 3] }));
    expect(screen.getByTestId('evaluation-conflict')).toHaveTextContent(
      'No answer key covers scenario v4, the version this run uses (existing: v2 (covers scenario v2); v3). Pick a compatible version',
    );
    r3.unmount();
    const r4 = show(conflict({ answer_key_version: 1, scenario_versions: 3, run_scenario_version: 1 }));
    expect(screen.getByTestId('evaluation-conflict')).toHaveTextContent('covers scenario v3,');
    r4.unmount();
    const r5 = show(conflict({ run_scenario_version: 4, answer_keys: [{ version: 5 }] }));
    expect(screen.getByTestId('evaluation-conflict')).toHaveTextContent('v5 (covers scenario all versions)');
    r5.unmount();
    show(conflict({ run_scenario_version: 4, answer_keys: [] }));
    expect(screen.getByTestId('evaluation-conflict')).toHaveTextContent(/^No answer key covers scenario v4, the version this run uses\. Pick/);
  });

  it('stays silent for other errors', () => {
    const { container } = show(conflict({}));
    expect(container).toBeEmptyDOMElement();
    show(conflict({ replay_of: 'r' }, 400));
    show(new Error('x'));
    expect(screen.queryByTestId('evaluation-conflict')).toBeNull();
  });
});
