export function JsonBlock({ value, label }: { value: unknown; label: string }) {
  const text = JSON.stringify(value, null, 2);
  if (!value || (typeof value === 'object' && Object.keys(value).length === 0)) {
    return <span className="muted">—</span>;
  }
  return (
    <details>
      <summary>{label}</summary>
      <pre className="json">{text}</pre>
    </details>
  );
}
