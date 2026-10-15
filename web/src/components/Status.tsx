import type { ReactNode } from 'react';

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p role="status" className="muted">
      {label}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="empty" data-testid="empty-state">
      {children}
    </p>
  );
}

export function Badge({ value, kind }: { value: string; kind?: string }) {
  return <span className={`badge badge-${(kind ?? value).toLowerCase()}`}>{value}</span>;
}
