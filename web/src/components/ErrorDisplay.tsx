import { isApiError } from '../api/client';

const HINTS: Record<string, string> = {
  PERMISSION_DENIED: 'Your API key’s role is not allowed to use this view. Connect with a key that has the required role.',
  UNAUTHENTICATED: 'The API key was rejected. Reconnect with a valid key.',
  RUN_NOT_ACTIVE: 'This operation needs a RUNNING run.',
  TIMEOUT: 'The server may be overloaded or unreachable. Try again.',
  NETWORK_ERROR: 'Check the base URL on the Connect page.',
  UPSTREAM_UNAVAILABLE:
    'The LLM provider behind the simulator is unavailable (outage, refusal or configuration). This is not caused by your request; retry later or check the server’s LLM settings.',
};

interface FieldError {
  loc: string[];
  msg: string;
}

function fieldErrors(details: Record<string, unknown>): FieldError[] {
  const errs = details.errors;
  if (!Array.isArray(errs)) return [];
  return errs
    .filter((e): e is { loc: unknown; msg: unknown } => !!e && typeof e === 'object')
    .map((e) => ({ loc: Array.isArray(e.loc) ? e.loc.map(String) : [], msg: String(e.msg ?? '') }));
}

/**
 * Renders an API error envelope (code, message, field errors, request id) as an alert. `hint`
 * replaces the generic advice for the error code (null: none), for callers whose own buttons
 * say what to do next.
 */
export function ErrorDisplay({ error, title, hint: hintOverride }: { error: unknown; title?: string; hint?: string | null }) {
  if (!isApiError(error)) {
    return (
      <div role="alert" className="error-box">
        <p className="error-title">{title ?? 'Something went wrong'}</p>
        <p>{error instanceof Error ? error.message : String(error)}</p>
      </div>
    );
  }
  const heading =
    error.kind === 'timeout'
      ? 'Request timed out'
      : error.kind === 'network'
        ? 'Network error'
        : `${error.code} (HTTP ${error.status})`;
  const fields = fieldErrors(error.details);
  const hint = hintOverride === undefined ? HINTS[error.code] : hintOverride;
  const otherDetails = Object.fromEntries(Object.entries(error.details).filter(([k]) => k !== 'errors'));
  return (
    <div role="alert" className="error-box" data-testid="api-error" data-error-code={error.code}>
      <p className="error-title">
        {title ? `${title}: ` : ''}
        {heading}
      </p>
      <p>{error.message}</p>
      {fields.length > 0 && (
        <ul aria-label="Field errors">
          {fields.map((f, i) => (
            <li key={i}>
              <code>{f.loc.filter((p) => p !== 'body').join('.') || '(request)'}</code>: {f.msg}
            </li>
          ))}
        </ul>
      )}
      {Object.keys(otherDetails).length > 0 && (
        <details>
          <summary>Details</summary>
          <pre>{JSON.stringify(otherDetails, null, 2)}</pre>
        </details>
      )}
      {hint && <p className="hint">{hint}</p>}
      {error.requestId && (
        <p className="request-id">
          Request ID: <code data-testid="request-id">{error.requestId}</code>
        </p>
      )}
    </div>
  );
}
