import { useEffect, useRef, useState } from 'react';
import { isInProgress } from '../hooks/useActionKey';
import { ErrorDisplay } from './ErrorDisplay';

/**
 * Shown when a slow create (run, evaluation) got no confirmation: it may have happened anyway.
 * While it is shown the form stays locked. "Check again" repeats the identical request with the
 * same Idempotency-Key: the server answers with what that request already did, or does it now
 * (records are kept for 24 hours). "Discard and start over" forgets the key; a new request can
 * then duplicate the work if the first one did succeed, so it asks first. Focus moves to the
 * heading when the notice appears so screen readers hear it.
 */
export function UnconfirmedNotice({
  error,
  what,
  onRetry,
  onDiscard,
}: {
  error: unknown;
  what: string;
  onRetry: () => void;
  onDiscard: () => void;
}) {
  const running = isInProgress(error);
  const heading = useRef<HTMLHeadingElement>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  return (
    <section className="card warning" aria-labelledby="unconfirmed-h" data-testid="unconfirmed">
      <h2 id="unconfirmed-h" tabIndex={-1} ref={heading}>
        {running ? `The server is still creating the ${what}` : `The ${what} was not confirmed`}
      </h2>
      <p>
        {running
          ? 'It is still working on your first attempt. Check again in a few seconds.'
          : `The server did not answer in time, so the ${what} may or may not exist yet.`}{' '}
        “Check again” sends the same request with the same idempotency key, so it cannot create a second {what}: you
        get the {what} your first attempt created, or it is created now. The form stays locked until you check again or
        start over.
      </p>
      <div className="row">
        <button type="button" className="primary" onClick={onRetry}>
          Check again
        </button>
        <button type="button" onClick={() => setConfirmDiscard(true)} aria-expanded={confirmDiscard}>
          Discard and start over
        </button>
      </div>
      {confirmDiscard && (
        <div className="notice" role="alert" data-testid="discard-warning">
          <p>
            If your first attempt did create the {what}, starting over and submitting again creates a second one. Check
            again first unless you are sure it failed.
          </p>
          <div className="row">
            <button type="button" className="danger" onClick={onDiscard}>
              Start over anyway
            </button>
            <button type="button" onClick={() => setConfirmDiscard(false)}>
              Keep this request
            </button>
          </div>
        </div>
      )}
      {/* the generic timeout advice ("Try again") would contradict the buttons above */}
      {!running && <ErrorDisplay error={error} title="Details" hint={null} />}
    </section>
  );
}
