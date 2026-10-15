import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { EvaluationReport, EvidenceRef } from '../api/types';
import { num, pct } from '../components/format';
import { Badge } from '../components/Status';
import { runLinks } from '../run/keys';
import { DiffView } from './DiffView';
import { ReportDownloads } from './ReportDownloads';

const CATEGORIES = ['task', 'safety', 'integration', 'efficiency', 'communication'];
const STATUSES = ['PASS', 'FAIL', 'INCONCLUSIVE', 'NOT_APPLICABLE'];

function evidenceHref(runId: string, e: EvidenceRef): string | null {
  switch (e.kind) {
    case 'event':
      return runLinks.event(runId, e.ref);
    case 'message':
      return runLinks.message(runId, e.ref);
    case 'action':
      return runLinks.action(runId, e.ref);
    case 'state':
      return runLinks.state(runId);
    default:
      return null;
  }
}

function Evidence({ runId, items }: { runId: string; items: EvidenceRef[] }) {
  if (items.length === 0) return <span className="muted">—</span>;
  return (
    <ul className="evidence">
      {items.map((e, i) => {
        const href = evidenceHref(runId, e);
        const text = `${e.kind} ${e.ref}${e.note ? ` (${e.note})` : ''}`;
        return <li key={i}>{href ? <Link to={href}>{text}</Link> : <span>{text}</span>}</li>;
      })}
    </ul>
  );
}

export function EvaluationReportView({ report }: { report: EvaluationReport }) {
  const [status, setStatus] = useState('');
  const outcomes = report.outcomes.filter((o) => !status || o.status === status);
  const categories = [...new Set([...CATEGORIES, ...Object.keys(report.metrics)])];

  return (
    <section aria-labelledby="report-h" className="report" data-testid="evaluation-report">
      <h2 id="report-h">
        Report <code>{report.evaluation_id}</code>
      </h2>
      <ReportDownloads runId={report.run_id} evaluationId={report.evaluation_id} />
      <div className={`verdict verdict-${report.verdict.toLowerCase()}`}>
        <p>
          Verdict: <Badge value={report.verdict} />
        </p>
        {(report.verdict_reasons?.length ?? 0) > 0 && (
          <ul aria-label="Verdict reasons" data-testid="verdict-reasons">
            {report.verdict_reasons?.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
      </div>
      <dl className="kv">
        <dt>Scenario</dt>
        <dd>
          {report.scenario_key} v{report.scenario_version}
        </dd>
        <dt>Answer key</dt>
        <dd>
          {report.answer_key_version == null ? 'none' : `v${report.answer_key_version}`}{' '}
          {report.answer_key_hash && <code>{report.answer_key_hash}</code>}
        </dd>
        <dt>Agent build</dt>
        <dd>{report.agent_build ?? '—'}</dd>
        <dt>Checks</dt>
        <dd>
          {report.checks_enabled ? 'on' : 'off'} (source: {report.checks_source})
        </dd>
        <dt>Evaluated</dt>
        <dd>{report.created_at}</dd>
      </dl>

      <h3>Accuracy</h3>
      <dl className="kv" data-testid="accuracy">
        <dt>Overall</dt>
        <dd>{pct(report.accuracy.overall)}</dd>
        <dt>Mandatory</dt>
        <dd>{pct(report.accuracy.mandatory)}</dd>
        <dt>Decided / passed</dt>
        <dd>
          {report.accuracy.decided ?? 0} / {report.accuracy.passed ?? 0}
        </dd>
      </dl>

      <h3>Metrics by category</h3>
      <table data-testid="metrics-table">
        <thead>
          <tr>
            <th scope="col">Category</th>
            <th scope="col">Passed</th>
            <th scope="col">Failed</th>
            <th scope="col">Inconclusive</th>
            <th scope="col">Not applicable</th>
            <th scope="col">Accuracy</th>
          </tr>
        </thead>
        <tbody>
          {categories.map((c) => {
            const m = report.metrics[c];
            return (
              <tr key={c}>
                <th scope="row">{c}</th>
                <td>{m?.passed ?? 0}</td>
                <td>{m?.failed ?? 0}</td>
                <td>{m?.inconclusive ?? 0}</td>
                <td>{m?.not_applicable ?? 0}</td>
                <td>{pct(m?.accuracy)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <h3>Efficiency</h3>
      <dl className="kv">
        <dt>Agent actions</dt>
        <dd>{report.efficiency.agent_actions}</dd>
        <dt>Denied actions</dt>
        <dd>{report.efficiency.denied_actions}</dd>
        <dt>Replies sent</dt>
        <dd>{report.efficiency.replies_sent}</dd>
        <dt>Virtual duration</dt>
        <dd>{num(report.efficiency.virtual_duration_seconds, 1)} s</dd>
        <dt>Model tokens (in / out)</dt>
        <dd>
          {report.efficiency.model_input_tokens} / {report.efficiency.model_output_tokens}
        </dd>
      </dl>

      <h3>Assertions</h3>
      <div className="field inline">
        <label htmlFor="status-filter">Status</label>
        <select id="status-filter" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      <table data-testid="assertions-table">
        <thead>
          <tr>
            <th scope="col">Assertion</th>
            <th scope="col">Status</th>
            <th scope="col">Category</th>
            <th scope="col">Severity</th>
            <th scope="col">Type / subject</th>
            <th scope="col">Reason</th>
            <th scope="col">Evidence</th>
          </tr>
        </thead>
        <tbody>
          {outcomes.map((o) => (
            <tr key={o.assertion_id} data-testid={`assertion-${o.assertion_id}`}>
              <th scope="row">
                {o.assertion_id}
                <details>
                  <summary>Expected vs actual</summary>
                  <DiffView diff={o.diff ?? { explanation: '' }} caption={`Diff for ${o.assertion_id}`} />
                </details>
              </th>
              <td>
                <Badge value={o.status} />
                {o.score != null && <span className="muted"> score {num(o.score, 2)}</span>}
              </td>
              <td>{o.category}</td>
              <td>{o.severity}</td>
              <td>
                {o.type} / {o.subject}
              </td>
              <td>{o.reason || '—'}</td>
              <td>
                <Evidence runId={report.run_id} items={o.evidence ?? []} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {outcomes.length === 0 && <p className="muted">No assertions with this status.</p>}
      <details>
        <summary>Reproducibility</summary>
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollable region must be keyboard-reachable (axe scrollable-region-focusable) */}
        <pre className="json" tabIndex={0} role="region" aria-label="Reproducibility details">
          {JSON.stringify(report.reproducibility ?? {}, null, 2)}
        </pre>
      </details>
    </section>
  );
}
