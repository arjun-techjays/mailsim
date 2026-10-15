import { summarizeStatus, useSystemStatus } from '../guide/systemStatus';

/** Header pill: server mode, LLM provider and IMAP/SMTP listeners (GET /v1/system/status). */
export function StatusPill() {
  const q = useSystemStatus();
  if (q.isPending) return null;
  if (q.isError) {
    return (
      <p className="status-pill" data-testid="status-pill">
        <span className="muted">Server status unavailable</span>
      </p>
    );
  }
  const s = summarizeStatus(q.data);
  return (
    <p className="status-pill" data-testid="status-pill">
      <span className="visually-hidden">Server status: </span>
      <span>{s.mode}</span>
      <span>{s.llm}</span>
      <span className={s.listenersUp ? undefined : 'pill-warn'}>{s.listeners}</span>
    </p>
  );
}
