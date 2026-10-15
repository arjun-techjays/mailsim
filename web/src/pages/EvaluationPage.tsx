import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import type { EvaluationRequest } from '../api/types';
import { useApi } from '../auth/session';
import { RequireRole } from '../components/Gates';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { PageIntro } from '../components/PageIntro';
import { WhatIsThis } from '../guide/Concepts';
import { pct } from '../components/format';
import { Badge, Empty, Loading } from '../components/Status';
import { EvaluationReportView } from '../evaluation/ReportView';
import { usePageTitle } from '../hooks/usePageTitle';
import { runKeys } from '../run/keys';

function TriggerForm({ runId, scenarioKey, onCreated }: { runId: string; scenarioKey: string | null; onCreated: (id: string) => void }) {
  const api = useApi();
  const qc = useQueryClient();
  const keys = useQuery({
    queryKey: ['answer-keys', scenarioKey],
    queryFn: () => api.listAnswerKeys(scenarioKey as string),
    enabled: !!scenarioKey,
  });
  const [version, setVersion] = useState('');
  const [checks, setChecks] = useState<'' | 'on' | 'off'>('');
  const [complete, setComplete] = useState(true);
  const mutation = useMutation({
    mutationFn: (body: EvaluationRequest) => api.evaluate(runId, body),
    onSuccess: (report) => {
      onCreated(report.evaluation_id);
      void qc.invalidateQueries({ queryKey: runKeys.all(runId) });
    },
  });
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    mutation.mutate({
      answer_key_version: version ? Number(version) : null,
      checks: checks || null,
      complete_run: complete,
    });
  };
  return (
    <section aria-labelledby="trigger-h" className="card">
      <h2 id="trigger-h">Evaluate this run</h2>
      <p className="hint">Applies an answer key to the recorded run. The agent is not re-run.</p>
      <form className="form" onSubmit={onSubmit}>
        <div className="field">
          <label htmlFor="ak-version">Answer-key version</label>
          <select id="ak-version" value={version} onChange={(e) => setVersion(e.target.value)}>
            <option value="">Latest compatible</option>
            {(keys.data ?? []).map((k) => (
              <option key={k.id} value={k.version}>
                v{k.version} ({k.source})
              </option>
            ))}
          </select>
          {keys.isError && <ErrorDisplay error={keys.error} title="Could not load answer keys" />}
        </div>
        <fieldset>
          <legend>Deterministic checks</legend>
          {(
            [
              ['', 'Run / answer-key default'],
              ['on', 'On'],
              ['off', 'Off'],
            ] as const
          ).map(([value, label]) => (
            <label key={value || 'default'} className="radio">
              <input type="radio" name="eval-checks" checked={checks === value} onChange={() => setChecks(value)} />
              {label}
            </label>
          ))}
        </fieldset>
        <label className="checkbox">
          <input type="checkbox" checked={complete} onChange={(e) => setComplete(e.target.checked)} />
          Complete the run (set PASSED/FAILED on a RUNNING run)
        </label>
        <button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? 'Evaluating…' : 'Run evaluation'}
        </button>
      </form>
      {mutation.isError && <ErrorDisplay error={mutation.error} title="Evaluation failed" />}
    </section>
  );
}

function EvaluationContent({ runId }: { runId: string }) {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const run = useQuery({ queryKey: runKeys.all(runId), queryFn: () => api.getRun(runId) });
  const evals = useQuery({ queryKey: runKeys.evaluations(runId), queryFn: () => api.listEvaluations(runId) });
  const selectedId = params.get('evaluation');
  const list = evals.data ?? [];
  const selected = list.find((r) => r.evaluation_id === selectedId) ?? list[list.length - 1];

  return (
    <>
      {run.isError && <ErrorDisplay error={run.error} title="Could not load run" />}
      <TriggerForm
        runId={runId}
        scenarioKey={run.data?.scenario_key ?? null}
        onCreated={(id) => setParams({ evaluation: id })}
      />
      <section aria-labelledby="evals-h">
        <h2 id="evals-h">Evaluations</h2>
        {evals.isPending ? (
          <Loading label="Loading evaluations…" />
        ) : evals.isError ? (
          <ErrorDisplay error={evals.error} title="Could not load evaluations" />
        ) : list.length === 0 ? (
          <Empty>This run has not been evaluated yet. Use “Evaluate this run” above.</Empty>
        ) : (
          <table data-testid="evaluations-table">
            <thead>
              <tr>
                <th scope="col">Evaluation</th>
                <th scope="col">Created</th>
                <th scope="col">Answer key</th>
                <th scope="col">Checks</th>
                <th scope="col">Verdict</th>
                <th scope="col">Accuracy</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.evaluation_id} className={r === selected ? 'selected' : undefined}>
                  <th scope="row">
                    <button
                      type="button"
                      className="link"
                      aria-current={r === selected ? 'true' : undefined}
                      onClick={() => setParams({ evaluation: r.evaluation_id })}
                    >
                      {r.evaluation_id}
                    </button>
                  </th>
                  <td>{r.created_at}</td>
                  <td>{r.answer_key_version == null ? '—' : `v${r.answer_key_version}`}</td>
                  <td>{r.checks_enabled ? 'on' : 'off'}</td>
                  <td>
                    <Badge value={r.verdict} />
                  </td>
                  <td>{pct(r.accuracy.overall)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      {selected && <EvaluationReportView report={selected} />}
    </>
  );
}

export function EvaluationPage() {
  const { runId = '' } = useParams();
  usePageTitle(`Evaluations · ${runId}`);
  return (
    <>
      <p>
        <Link to={`/runs/${encodeURIComponent(runId)}`}>← Run {runId}</Link>
      </p>
      <h1>
        Evaluations for <code>{runId}</code>
      </h1>
      <PageIntro next={[{ to: `/runs/${encodeURIComponent(runId)}`, label: 'Back to the run' }]}>
        <p>
          Grade what the agent did in this run against the scenario’s hidden answer key. Each report explains every
          assertion with evidence from the event trace and mailboxes. Evaluating again (another answer-key version,
          checks on/off) adds a new report.
        </p>
        <WhatIsThis term="evaluation" />
      </PageIntro>
      <RequireRole roles={['evaluator']} what="Evaluation results">
        <EvaluationContent runId={runId} />
      </RequireRole>
    </>
  );
}
