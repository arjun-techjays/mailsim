// Live validation status (badge) and the error summary that links to each offending field.
import { useLayoutEffect, useState } from 'react';
import { fieldId, SECTIONS, type IndexedIssue } from './paths';

export type ValidationState = 'validating' | 'valid' | 'invalid' | 'error';

export function StatusBadge({ state, count }: { state: ValidationState; count: number }) {
  const text =
    state === 'validating'
      ? 'Validating…'
      : state === 'valid'
        ? 'Valid: ready to save'
        : state === 'error'
          ? 'Could not validate'
          : `${count} ${count === 1 ? 'problem' : 'problems'} to fix`;
  return (
    <span role="status" aria-live="polite" className={`badge b-status b-status-${state}`} data-testid="validation-status">
      {text}
    </span>
  );
}

const sectionLabel = (id: string) => SECTIONS.find((s) => s.id === id)?.label ?? id;

export function ErrorSummary({ issues, onGoTo }: { issues: IndexedIssue[]; onGoTo: (issue: IndexedIssue) => void }) {
  // Which issues have a field of their own (the others are only listed here). The DOM holds
  // every section (inactive ones are hidden), so this can be checked after each render.
  const [exact, setExact] = useState<Set<string>>(new Set());
  useLayoutEffect(() => {
    const next = new Set(issues.filter((i) => document.getElementById(fieldId(i.key))).map((i) => i.key));
    setExact((prev) => (prev.size === next.size && [...next].every((k) => prev.has(k)) ? prev : next));
  }, [issues]);
  if (issues.length === 0) return null;
  return (
    <details className="error-box b-summary" open data-testid="error-summary">
      <summary>
        <strong>
          {issues.length} {issues.length === 1 ? 'problem' : 'problems'} to fix before saving
        </strong>
      </summary>
      <ul>
        {issues.map((issue, i) => (
          <li key={`${issue.key}-${i}`}>
            <button type="button" className="link" onClick={() => onGoTo(issue)}>
              {sectionLabel(issue.section)}: {issue.message}
            </button>{' '}
            <code className="muted">{issue.key}</code>
            {!exact.has(issue.key) && <span className="muted"> (not tied to a single field)</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}
