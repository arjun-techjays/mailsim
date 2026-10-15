// Helpers for the live activity panel.

const INTERFACE_LABELS: Record<string, string> = {
  rest: 'REST API',
  gmail: 'Gmail API',
  graph: 'Microsoft Graph',
  mcp_http: 'MCP (HTTP)',
  mcp_stdio: 'MCP (stdio)',
  imap: 'IMAP',
  smtp: 'SMTP',
  api: 'API',
  cli: 'CLI',
  dashboard: 'Dashboard',
};

export function interfaceLabel(iface: string | null | undefined): string {
  if (!iface) return 'Unknown';
  return INTERFACE_LABELS[iface] ?? iface;
}

function short(v: unknown, max = 40): string {
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** {folder: "inbox", message_id: "m1"} -> "folder=inbox, message_id=m1" (at most ~120 chars). */
export function summarizeInput(input: Record<string, unknown> | null | undefined): string {
  const parts = Object.entries(input ?? {}).map(([k, v]) => `${k}=${short(v)}`);
  const text = parts.join(', ');
  return text.length > 120 ? `${text.slice(0, 119)}…` : text;
}

/** Badge class for an outcome: ok, denied, or an error code. */
export function outcomeKind(outcome: string): string {
  if (outcome === 'ok') return 'ok';
  if (outcome === 'denied') return 'denied';
  return 'error-code';
}
