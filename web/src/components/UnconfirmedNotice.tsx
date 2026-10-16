import { isInProgress } from '../hooks/useActionKey';
import { ErrorDisplay } from './ErrorDisplay';

/**
 * Shown when a slow create (run, evaluation) got no confirmation: it may have happened anyway.
 * "Check again" repeats the identical request with the same Idempotency-Key, which can never
 * do the work twice: the server answers with the existing result, or does it now.
 */
export function UnconfirmedNotice({ error, what, onRetry }: { error: unknown; what: string; onRetry: () => void }) {
  const running = isInProgress(error);
  return (
    <section className="card warning" aria-labelledby="unconfirmed-h" data-testid="unconfirmed">
      <h2 id="unconfirmed-h">
        {running ? `The server is still creating the ${what}` : `The ${what} was not confirmed`}
      </h2>
      <p role="status">
        {running
          ? `It is still working on your first attempt. Check again in a few seconds.`
          : `The server did not answer in time, so the ${what} may or may not exist yet.`}{' '}
        Checking again repeats the same request with the same idempotency key: it never creates a second {what}. You
        get the {what} if it was created, or it is created now.
      </p>
      <button type="button" className="primary" onClick={onRetry}>
        Check again
      </button>
      {!running && <ErrorDisplay error={error} title="Details" />}
    </section>
  );
}
