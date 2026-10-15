import { useState } from 'react';

export function CopyButton({ value, label }: { value: string; label: string }) {
  const [status, setStatus] = useState<'' | 'copied' | 'failed'>('');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setStatus('copied');
    } catch {
      setStatus('failed');
    }
  };
  return (
    <span className="copy">
      <button type="button" onClick={() => void copy()}>
        {label}
      </button>
      <span role="status" aria-live="polite" className="muted">
        {status === 'copied' ? 'Copied to clipboard.' : status === 'failed' ? 'Copy failed: select the text and copy it manually.' : ''}
      </span>
    </span>
  );
}
