import type { AssertionDiff } from '../api/types';
import { diffRows } from './diff';

/** Expected (answer key) versus actual (recorded run), field by field. Differences are marked in text. */
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
              <tr key={r.key} className={r.same ? undefined : 'diff-changed'}>
                <th scope="row">
                  <code>{r.key}</code>
                </th>
                <td>
                  <code>{r.expected}</code>
                </td>
                <td>
                  <code>{r.actual}</code>
                </td>
                <td>{r.same ? 'same' : 'differs'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p>
        Matched: <strong>{diff.matched == null ? 'not compared' : diff.matched ? 'yes' : 'no'}</strong>
        {diff.explanation ? ` — ${diff.explanation}` : ''}
      </p>
    </div>
  );
}
