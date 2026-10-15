// Success message shown on the scenario page right after the builder published a version.
import { Link, useLocation } from 'react-router-dom';
import './builder.css';

interface PublishedState {
  published?: { version: number; mode: string };
}

/** Where evaluators write a scenario's answer key (answer-key editor workstream). */
const answerKeyPath = (key: string) => `/scenarios/${encodeURIComponent(key)}/answer-key`;

export function PublishedNotice({ scenarioKey }: { scenarioKey: string }) {
  const state = useLocation().state as PublishedState | null;
  const published = state?.published;
  if (!published) return null;
  const key = encodeURIComponent(scenarioKey);
  return (
    <section className="b-published" role="status" aria-labelledby="published-h" data-testid="published-notice">
      <h2 id="published-h">Published version {published.version}</h2>
      <p>
        <code>{scenarioKey}</code> v{published.version} is saved. It is immutable: later changes become new versions.
      </p>
      <p>Next steps:</p>
      <ol>
        <li>
          <Link to={answerKeyPath(scenarioKey)}>Write its answer key</Link> — how runs of this scenario are graded.
        </li>
        <li>
          <Link to={`/scenarios/${key}/start`}>Start a run</Link> — get an agent key and connect your agent.
        </li>
      </ol>
    </section>
  );
}
