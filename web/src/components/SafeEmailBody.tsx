import { useId, useState } from 'react';

import { wrapHtml } from './emailHtml';

// Untrusted email HTML is only ever rendered via wrapHtml() inside a sandbox="" iframe.
export function SafeEmailBody({ text, html }: { text: string | null | undefined; html: string | null | undefined }) {
  const hasText = !!text;
  const hasHtml = !!html;
  const [mode, setMode] = useState<'text' | 'html'>(hasText || !hasHtml ? 'text' : 'html');
  const id = useId();

  return (
    <section aria-labelledby={`${id}-h`} className="email-body">
      <div className="row space-between">
        <h4 id={`${id}-h`}>Body</h4>
        {hasHtml && hasText && (
          <button type="button" onClick={() => setMode(mode === 'text' ? 'html' : 'text')} aria-pressed={mode === 'html'}>
            {mode === 'text' ? 'Show HTML (sandboxed)' : 'Show plain text'}
          </button>
        )}
      </div>
      {mode === 'text' ? (
        hasText ? (
          <pre className="email-text" data-testid="email-text">
            {text}
          </pre>
        ) : (
          <p className="muted">This message has no body.</p>
        )
      ) : (
        <>
          <p className="hint">
            HTML is shown in a sandbox: scripts, forms and remote content are blocked.
            {!hasText && ' This message has no plain-text part.'}
          </p>
          <iframe
            data-testid="email-html-frame"
            title="Email HTML body (sandboxed, scripts disabled)"
            sandbox=""
            referrerPolicy="no-referrer"
            srcDoc={wrapHtml(html as string)}
            className="email-frame"
          />
        </>
      )}
    </section>
  );
}
