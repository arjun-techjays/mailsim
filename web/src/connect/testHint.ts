import type { ConnectionTestResult } from '../api/types';
import { START_PROTOCOLS } from './snippets';

const AUTH_CODES = ['UNAUTHENTICATED', 'AUTHENTICATIONFAILED', 'AUTHENTICATION_FAILED', 'InvalidAuthenticationToken'];

/** What to do about a failed test, by error code. */
export function testHint(r: ConnectionTestResult): string {
  const code = r.error_code ?? '';
  if (AUTH_CODES.includes(code)) {
    return 'The key was rejected: it may have been revoked, belong to another run, or the run may have ended. Mint a new agent key above and test again.';
  }
  if (code === 'RUN_NOT_ACTIVE') return 'The run is not RUNNING. Start it from the run page, then test again.';
  if (code === 'PERMISSION_DENIED' || code === 'ErrorAccessDenied') {
    return 'The key is valid but is not an agent key for this mailbox. Use this run’s agent key.';
  }
  if (r.interface === 'imap' || r.interface === 'smtp') {
    return `Is the listener running? Start it with: ${START_PROTOCOLS}`;
  }
  if (r.interface === 'mcp_stdio') {
    return 'The server could not start mcp-stdio. Check that it runs from the mailsim checkout with the database settings.';
  }
  return 'Check the server log for details.';
}
