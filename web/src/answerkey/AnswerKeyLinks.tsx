// Entry points into the answer-key editor, used on the scenario page.
import { useEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';
import './answerkey.css';

const editorHref = (scenarioKey: string, from?: number) =>
  `/scenarios/${encodeURIComponent(scenarioKey)}/answer-key${from != null ? `?from=${from}` : ''}`;

export function NewAnswerKeyLink({ scenarioKey }: { scenarioKey: string }) {
  return (
    <Link className="button" to={editorHref(scenarioKey)}>
      Write a new answer key
    </Link>
  );
}

export function EditAnswerKeyLink({ scenarioKey, version }: { scenarioKey: string; version: number }) {
  return (
    <Link to={editorHref(scenarioKey, version)} aria-label={`Edit answer key v${version} as a new version`}>
      Edit as new version
    </Link>
  );
}

/** Shown on the scenario page after the editor published a key (router state). */
export function AnswerKeySavedNotice({ scenarioKey }: { scenarioKey: string }) {
  const location = useLocation();
  const saved = (location.state as { answerKeySaved?: unknown } | null)?.answerKeySaved;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (typeof saved === 'number') ref.current?.focus();
  }, [saved]);
  if (typeof saved !== 'number') return null;
  const s = encodeURIComponent(scenarioKey);
  return (
    <div className="card ak-saved" role="status" tabIndex={-1} ref={ref} data-testid="answer-key-saved">
      <p className="error-title">Answer key v{saved} published.</p>
      <p>It is stored apart from the scenario and is never shown to the agent. Next you can:</p>
      <ul>
        <li>
          <Link to="/runs">Open a run of this scenario</Link> and evaluate it with v{saved} (Evaluations page, answer-key
          version {saved}).
        </li>
        <li>
          <Link to={`/scenarios/${s}/compare`}>Compare agent builds</Link> graded with v{saved}.
        </li>
        <li>
          <Link to={editorHref(scenarioKey, saved)}>Refine it further</Link> (publishes a new version).
        </li>
      </ul>
    </div>
  );
}
