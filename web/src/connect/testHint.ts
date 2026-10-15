import type { ConnectionTestResult } from '../api/types';
import { START_PROTOCOLS } from './snippets';

/**
 * What to do about a failed test. Contract (UX-B): invalid/revoked/expired keys give
 * UNAUTHENTICATED (details.reason "invalid_key"); a valid key that is not this run's agent key
 * gives PERMISSION_DENIED (details.reason "not_agent_key" | "other_run"); a stopped IMAP/SMTP
 * listener gives LISTENER_DOWN.
 */
export function testHint(r: ConnectionTestResult): string {
  const reason = typeof r.details?.reason === 'string' ? r.details.reason : '';
  switch (r.error_code) {
    case 'UNAUTHENTICATED':
      return 'The key is not valid: it may be mistyped, revoked, or the run may have ended. Mint a new agent key above and test again.';
    case 'PERMISSION_DENIED':
      if (reason === 'not_agent_key') {
        return 'This is a controller or evaluator key. Agents must use the run’s agent key: paste it or mint a new one above.';
      }
      if (reason === 'other_run') return 'This agent key belongs to another run. Use this run’s key, or mint a new one above.';
      return 'The key is not allowed to use this run’s mailbox. Use this run’s agent key.';
    case 'LISTENER_DOWN':
      return `The ${r.interface.toUpperCase()} listener is not running. Start it next to the API server: ${START_PROTOCOLS}`;
    case 'RUN_NOT_ACTIVE':
      return 'The run is not RUNNING. Start it from the run page, then test again.';
    default:
      if (r.interface === 'mcp_stdio') {
        return 'The server could not start mcp-stdio. Check that it runs from the mailsim checkout with the database settings.';
      }
      return 'Check the server log for details.';
  }
}
