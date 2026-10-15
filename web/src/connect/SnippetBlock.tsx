import { useId } from 'react';
import { CopyButton } from '../components/CopyButton';
import type { Snippet } from './snippets';

/** A titled, copyable code sample (plain <pre><code>; no inline styles or scripts). */
export function SnippetBlock({ snippet }: { snippet: Snippet }) {
  const id = useId();
  return (
    <figure className="snippet" data-testid={`snippet-${snippet.id}`}>
      <figcaption>
        <span id={id}>{snippet.title}</span>
        <CopyButton value={snippet.code} label={`Copy ${snippet.title}`} />
      </figcaption>
      {/* Scrollable region: focusable so keyboard users can scroll long lines. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <pre tabIndex={0} role="region" aria-labelledby={id} className={`code lang-${snippet.language}`}>
        <code>{snippet.code}</code>
      </pre>
    </figure>
  );
}
