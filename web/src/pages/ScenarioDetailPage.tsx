import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { useApi } from '../auth/session';
import { ShowFor } from '../components/Gates';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { Empty, Loading } from '../components/Status';
import { usePageTitle } from '../hooks/usePageTitle';
import { PublishedNotice } from '../builder/PublishedNotice'; // builder (UX-F1)

function AnswerKeys({ scenarioKey }: { scenarioKey: string }) {
  const api = useApi();
  const q = useQuery({ queryKey: ['answer-keys', scenarioKey], queryFn: () => api.listAnswerKeys(scenarioKey) });
  return (
    <section aria-labelledby="ak-h">
      <h2 id="ak-h">Answer-key versions</h2>
      <p className="hint">Evaluator only. Metadata only: grading content is never shown here or to agents.</p>
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorDisplay error={q.error} title="Could not load answer keys" />
      ) : q.data.length === 0 ? (
        <Empty>No answer keys yet.</Empty>
      ) : (
        <table data-testid="answer-keys">
          <thead>
            <tr>
              <th scope="col">Version</th>
              <th scope="col">Source</th>
              <th scope="col">Compatible scenario versions</th>
              <th scope="col">Spec hash</th>
              <th scope="col">Created</th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((k) => (
              <tr key={k.id}>
                <th scope="row">v{k.version}</th>
                <td>{k.source}</td>
                <td>{k.scenario_versions ? k.scenario_versions.map((v) => `v${v}`).join(', ') : 'all'}</td>
                <td>
                  <code>{k.spec_hash}</code>
                </td>
                <td>{k.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function ScenarioDetailPage() {
  const { key = '' } = useParams();
  usePageTitle(`Scenario ${key}`);
  const api = useApi();
  const q = useQuery({ queryKey: ['scenario', key], queryFn: () => api.getScenario(key) });

  return (
    <>
      <p>
        <Link to="/scenarios">← All scenarios</Link>
      </p>
      <h1>
        Scenario <code>{key}</code>
      </h1>
      <PublishedNotice scenarioKey={key} /> {/* builder (UX-F1) */}
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorDisplay error={q.error} title="Could not load scenario" />
      ) : (
        <>
          <div className="row">
            <ShowFor roles={['controller']}>
              <Link className="button" to={`/scenarios/${encodeURIComponent(key)}/start`}>
                Start a run
              </Link>
            </ShowFor>
            {/* ---- builder (UX-F1) */}
            {q.data.latest_version && (
              <ShowFor roles={['controller']}>
                <Link
                  className="button"
                  to={`/scenarios/${encodeURIComponent(key)}/edit?from=${q.data.latest_version.version}`}
                >
                  Edit (new version)
                </Link>
                <Link
                  className="button"
                  to={`/scenarios/${encodeURIComponent(key)}/clone?from=${q.data.latest_version.version}`}
                >
                  Clone
                </Link>
              </ShowFor>
            )}
            {/* ---- end builder (UX-F1) */}
            <ShowFor roles={['evaluator']}>
              <Link className="button" to={`/scenarios/${encodeURIComponent(key)}/compare`}>
                Compare builds
              </Link>
            </ShowFor>
          </div>
          <section aria-labelledby="latest-h">
            <h2 id="latest-h">Latest version</h2>
            {q.data.latest_version ? (
              <dl className="kv">
                <dt>Version</dt>
                <dd>v{q.data.latest_version.version}</dd>
                <dt>Version id</dt>
                <dd>
                  <code>{q.data.latest_version.id}</code>
                </dd>
                <dt>Spec hash</dt>
                <dd>
                  <code>{q.data.latest_version.spec_hash}</code>
                </dd>
                <dt>Published</dt>
                <dd>{q.data.latest_version.created_at}</dd>
              </dl>
            ) : (
              <Empty>No published version.</Empty>
            )}
          </section>
          <section aria-labelledby="versions-h">
            <h2 id="versions-h">Versions</h2>
            <ul data-testid="scenario-versions">
              {q.data.versions.map((v) => (
                <li key={v}>v{v}</li>
              ))}
            </ul>
          </section>
          <ShowFor roles={['evaluator']}>
            <AnswerKeys scenarioKey={key} />
          </ShowFor>
        </>
      )}
    </>
  );
}
