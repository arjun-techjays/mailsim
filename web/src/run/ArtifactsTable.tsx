import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { ArtifactOut } from '../api/types';
import { useApi } from '../auth/session';
import { saveBlob } from '../components/download';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { bytes } from '../components/format';
import { Empty, Loading } from '../components/Status';
import { runKeys } from './keys';

/** ArtifactOut.url (QA-A3; absent on older servers, null for the trace). */
const urlOf = (a: ArtifactOut): string | null => {
  const u = (a as ArtifactOut & { url?: unknown }).url;
  return typeof u === 'string' && u ? u : null;
};

function DownloadArtifact({ a, url }: { a: ArtifactOut; url: string }) {
  const api = useApi();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const name = a.ref.split('/').pop() || a.ref;
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const { blob, filename } = await api.download(url);
      // Opaque bytes, saved as a file: never rendered in the dashboard.
      saveBlob(new Blob([blob], { type: 'application/octet-stream' }), filename ?? name);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" className="link" disabled={busy} aria-label={`Download ${a.ref}`} onClick={() => void go()}>
        {busy ? 'Downloading…' : 'Download'}
      </button>
      {error != null && <ErrorDisplay error={error} title={`Could not download ${a.ref}`} />}
    </>
  );
}

export function ArtifactsTable({ runId, highlight }: { runId: string; highlight: string | null }) {
  const api = useApi();
  const q = useQuery({ queryKey: runKeys.artifacts(runId), queryFn: () => api.artifacts(runId) });
  if (q.isPending) return <Loading label="Loading artifacts…" />;
  if (q.isError) return <ErrorDisplay error={q.error} title="Could not load artifacts" />;
  return (
    <section aria-labelledby="artifacts-h">
      <h2 id="artifacts-h">Artifacts</h2>
      <p className="hint">
        Raw messages, attachments and reports can be downloaded as files (never opened here). The event trace has no file:
        read it in the Event trace tab.
      </p>
      {q.data.length === 0 ? (
        <Empty>No artifacts yet. Raw messages, attachments and traces appear here as the run produces them.</Empty>
      ) : (
        <table data-testid="artifacts-table">
          <thead>
            <tr>
              <th scope="col">Reference</th>
              <th scope="col">Kind</th>
              <th scope="col">Content type</th>
              <th scope="col">Size</th>
              <th scope="col">File</th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((a) => (
              <tr
                key={`${a.kind}:${a.ref}`}
                className={highlight === a.ref ? 'highlight' : undefined}
                data-testid={highlight === a.ref ? 'highlighted-artifact' : undefined}
              >
                <th scope="row">
                  <code>{a.ref}</code>
                </th>
                <td>{a.kind}</td>
                <td>{a.content_type}</td>
                <td>{bytes(a.size)}</td>
                <td>{urlOf(a) ? <DownloadArtifact a={a} url={urlOf(a) as string} /> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
