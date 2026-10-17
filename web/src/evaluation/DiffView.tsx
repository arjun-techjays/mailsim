import type { AssertionDiff } from '../api/types';
import { diffRows } from './diff';

/**
 * Expected (answer-key criterion) versus actual (recorded run), field by field. The assertion's
 * status is the verdict; the Comparison column only explains each field and only says "differs"
 * for a field of an assertion that did not match.
 */
export function DiffView({ diff, caption }: { diff: AssertionDiff; caption: string }) {
  const rows = diffRows(diff);
  return (
    <div className="diff">
      {rows.length === 0 ? (
        <p className="muted">No expected/actual values recorded.</p>
      ) : (
        <table data-testid="assertion-diff">
          <caption>{caption}</caption>
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Expected</th>
              <th scope="col">Actual</th>
              <th scope="col">Comparison</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className={r.flagged ? 'diff-changed' : undefined} data-comparison={r.comparison}>
                <th scope="row">
                  <code>{r.key}</code>
                </th>
                <td>
                  <code>{r.expected}</code>
                </td>
                <td>
                  <code>{r.actual}</code>
                </td>
                <td>{r.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rows.length > 0 && (
        <p className="hint">
          Expected shows the answer key’s criteria, Actual what the run recorded; a field on one side only is not a
          mismatch.
        </p>
      )}
      <p>
        Matched: <strong>{diff.matched == null ? 'not compared' : diff.matched ? 'yes' : 'no'}</strong>
        {diff.explanation ? ` — ${diff.explanation}` : ''}
      </p>
    </div>
  );
}
