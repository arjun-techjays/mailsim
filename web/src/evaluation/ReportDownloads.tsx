import { useState } from 'react';
import type { ReportFormat } from '../api/types';
import { useApi } from '../auth/session';
import { saveBlob } from '../components/download';
import { ErrorDisplay } from '../components/ErrorDisplay';

const FORMATS: { format: ReportFormat; label: string; ext: string }[] = [
  { format: 'json', label: 'JSON', ext: 'json' },
  { format: 'junit', label: 'JUnit XML', ext: 'xml' },
  { format: 'markdown', label: 'Markdown', ext: 'md' },
];

/**
 * The report export needs the bearer key, so it cannot be a plain link: fetch it, then hand the
 * browser a short-lived Blob URL to save (no navigation, nothing persisted). The file is named
 * as the server says (Content-Disposition), else `<run>-<evaluation>.<ext>`.
 */
export function ReportDownloads({ runId, evaluationId }: { runId: string; evaluationId: string }) {
  const api = useApi();
  const [busy, setBusy] = useState<ReportFormat | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState('');

  const download = async (format: ReportFormat, ext: string) => {
    setBusy(format);
    setError(null);
    setDone('');
    try {
      const { text, filename } = await api.report(runId, evaluationId, format);
      const name = filename ?? `${runId}-${evaluationId}.${ext}`;
      // Opaque bytes: a download never renders on the dashboard origin, whatever the server type.
      saveBlob(new Blob([text], { type: 'application/octet-stream' }), name);
      setDone(`Downloaded ${name}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="row" role="group" aria-label="Download report">
      {FORMATS.map(({ format, label, ext }) => (
        <button key={format} type="button" disabled={busy !== null} onClick={() => void download(format, ext)}>
          {busy === format ? 'Downloading…' : `Download ${label}`}
        </button>
      ))}
      <span role="status" className="muted" data-testid="report-download-status">
        {done}
      </span>
      {error != null && <ErrorDisplay error={error} title="Could not download report" />}
    </div>
  );
}
