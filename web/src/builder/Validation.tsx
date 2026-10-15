// Live validation status (badge) and the error summary that links to each offending field.
import { useEffect, useLayoutEffect, useState, type Ref } from 'react';
import { fieldId, SECTIONS, type IndexedIssue } from './paths';

export type ValidationState = 'validating' | 'valid' | 'invalid' | 'error';

function statusText(state: ValidationState, count: number): string {
  if (state === 'validating') return 'Validating…';
  if (state === 'valid') return 'Valid: ready to save';
  if (state === 'error') return 'Could not validate';
  return `${count} ${count === 1 ? 'problem' : 'problems'} to fix`;
}

/** Visible status, plus a live region that only announces settled results (not "Validating…"). */
export function StatusBadge({ state, count }: { state: ValidationState; count: number }) {
  const text = statusText(state, count);
  const [announced, setAnnounced] = useState('');
  useEffect(() => {
    if (state !== 'validating') setAnnounced(text);
  }, [state, text]);
  return (
    <>
      <span className={`badge b-status b-status-${state}`} data-testid="validation-status">
        {text}
      </span>
      <span role="status" aria-live="polite" className="visually-hidden" data-testid="validation-announcer">
        {announced}
      </span>
    </>
  );
}

const LABELS: Record<string, string> = Object.fromEntries(SECTIONS.map((s) => [s.id, s.label]));

export function ErrorSummary({ issues, onGoTo, ref }: { issues: IndexedIssue[]; onGoTo: (issue: IndexedIssue) => void; ref?: Ref<HTMLDetailsElement> }) {
  // Which issues have a field of their own (the others are only listed here). The DOM holds
  // every section (inactive ones are hidden), so this can be checked after each render.
  const [exact, setExact] = useState<Set<string>>(new Set());
  useLayoutEffect(() => {
    const next = new Set(issues.filter((i) => document.getElementById(fieldId(i.key))).map((i) => i.key));
    setExact((prev) => (prev.size === next.size && [...next].every((k) => prev.has(k)) ? prev : next));
  }, [issues]);
  if (issues.length === 0) return null;
  return (
    <details ref={ref} tabIndex={-1} className="error-box b-summary" open data-testid="error-summary">
      <summary>
        <strong>
          {issues.length} {issues.length === 1 ? 'problem' : 'problems'} to fix before saving
        </strong>
      </summary>
      <ul>
        {issues.map((issue, i) => (
          <li key={`${issue.key}-${i}`}>
            <button type="button" className="link" onClick={() => onGoTo(issue)}>
              {LABELS[issue.section]}: {issue.message}
            </button>{' '}
            <code className="muted">{issue.key}</code>
            {!exact.has(issue.key) && <span className="muted"> (not tied to a single field)</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}
