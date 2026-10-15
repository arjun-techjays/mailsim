import { useId } from 'react';

export function JsonBlock({ value, label }: { value: unknown; label: string }) {
  const id = useId();
  const text = JSON.stringify(value, null, 2);
  if (!value || (typeof value === 'object' && Object.keys(value).length === 0)) {
    return <span className="muted">—</span>;
  }
  return (
    <details>
      <summary id={id}>{label}</summary>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollable region must be keyboard-reachable (axe scrollable-region-focusable) */}
      <pre className="json" tabIndex={0} role="region" aria-labelledby={id}>
        {text}
      </pre>
    </details>
  );
}
