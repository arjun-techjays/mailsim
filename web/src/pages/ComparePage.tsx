import { useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { BuildComparison } from '../api/types';
import { useApi } from '../auth/session';
import { BarChart } from '../components/BarChart';
import { RequireRole } from '../components/Gates';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { delta, num, pct, splitList } from '../components/format';
import { Empty, Loading } from '../components/Status';
import { usePageTitle } from '../hooks/usePageTitle';

interface Params {
  answerKeyVersion?: number;
  builds: string[];
  baseline?: string;
}

function ComparisonView({ data }: { data: BuildComparison }) {
  const builds = Object.keys(data.builds);
  if (builds.length === 0) return <Empty>No evaluated runs for these builds.</Empty>;
  const assertions = [...new Set(builds.flatMap((b) => Object.keys(data.builds[b]?.per_assertion_pass_rate ?? {})))].sort();
  const d = (b: string, metric: string) => (b === data.baseline ? 'baseline' : delta(data.deltas?.[b]?.[metric]));

  return (
    <>
      <p>
        Answer key v{data.answer_key_version}; baseline: <strong>{data.baseline ?? 'none'}</strong>
      </p>
      <BarChart
        title="Mean accuracy by build"
        bars={builds.map((b) => ({
          label: b,
          value: data.builds[b]?.mean_accuracy ?? null,
          note: `${data.builds[b]?.runs ?? 0} runs`,
        }))}
        format={pct}
      />
      <table data-testid="comparison-table">
        <caption>Accuracy across repeated trials</caption>
        <thead>
          <tr>
            <th scope="col">Build</th>
            <th scope="col">Runs</th>
            <th scope="col">Mean accuracy</th>
            <th scope="col">Std dev</th>
            <th scope="col">Variance</th>
            <th scope="col">Pass rate</th>
            <th scope="col">Δ mean accuracy</th>
            <th scope="col">Δ pass rate</th>
          </tr>
        </thead>
        <tbody>
          {builds.map((b) => {
            const s = data.builds[b];
            return (
              <tr key={b}>
                <th scope="row">{b}</th>
                <td>{s?.runs}</td>
                <td>{pct(s?.mean_accuracy)}</td>
                <td>{num(s?.stdev)}</td>
                <td>{num(s?.variance, 4)}</td>
                <td>{pct(s?.pass_rate)}</td>
                <td>{d(b, 'mean_accuracy')}</td>
                <td>{d(b, 'pass_rate')}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="hint">Variance and standard deviation are n/a for builds with fewer than two runs.</p>
      <h2>Per-assertion pass rate</h2>
      {assertions.length === 0 ? (
        <Empty>No per-assertion data.</Empty>
      ) : (
        <table data-testid="per-assertion-table">
          <thead>
            <tr>
              <th scope="col">Assertion</th>
              {builds.map((b) => (
                <th scope="col" key={b}>
                  {b}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {assertions.map((a) => (
              <tr key={a}>
                <th scope="row">{a}</th>
                {builds.map((b) => (
                  <td key={b}>{pct(data.builds[b]?.per_assertion_pass_rate?.[a])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function CompareContent({ scenarioKey }: { scenarioKey: string }) {
  const api = useApi();
  const [params, setParams] = useState<Params>({ builds: [] });
  const [akv, setAkv] = useState('');
  const [builds, setBuilds] = useState('');
  const [baseline, setBaseline] = useState('');
  const q = useQuery({
    queryKey: ['accuracy', scenarioKey, params],
    queryFn: () => api.accuracy(scenarioKey, params),
  });
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setParams({
      answerKeyVersion: akv ? Number(akv) : undefined,
      builds: splitList(builds),
      baseline: baseline.trim() || undefined,
    });
  };
  return (
    <>
      <form className="form inline" onSubmit={onSubmit} aria-label="Comparison options">
        <div className="field">
          <label htmlFor="cmp-akv">Answer-key version</label>
          <input id="cmp-akv" type="number" min={1} value={akv} onChange={(e) => setAkv(e.target.value)} placeholder="latest" />
        </div>
        <div className="field">
          <label htmlFor="cmp-builds">Builds (comma-separated)</label>
          <input id="cmp-builds" value={builds} onChange={(e) => setBuilds(e.target.value)} placeholder="all" />
        </div>
        <div className="field">
          <label htmlFor="cmp-baseline">Baseline build</label>
          <input id="cmp-baseline" value={baseline} onChange={(e) => setBaseline(e.target.value)} placeholder="default" />
        </div>
        <button type="submit">Compare</button>
      </form>
      {q.isPending ? (
        <Loading label="Loading comparison…" />
      ) : q.isError ? (
        <ErrorDisplay error={q.error} title="Could not load comparison" />
      ) : (
        <ComparisonView data={q.data} />
      )}
    </>
  );
}

export function ComparePage() {
  const { key = '' } = useParams();
  usePageTitle(`Compare builds · ${key}`);
  return (
    <>
      <p>
        <Link to={`/scenarios/${encodeURIComponent(key)}`}>← Scenario {key}</Link>
      </p>
      <h1>
        Build comparison: <code>{key}</code>
      </h1>
      <RequireRole roles={['evaluator']} what="Build comparison">
        <CompareContent scenarioKey={key} />
      </RequireRole>
    </>
  );
}
