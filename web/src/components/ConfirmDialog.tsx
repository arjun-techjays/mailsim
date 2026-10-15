import { useEffect, useId, useRef, type ReactNode } from 'react';

/**
 * Inline confirmation for destructive actions (alertdialog pattern): focus moves to "Cancel"
 * when it opens, Escape cancels, and focus returns to the opener via `onCancel`'s caller.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const id = useId();
  const cancel = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const onCancelRef = useRef(onCancel);
  onCancelRef.current = onCancel;
  useEffect(() => {
    cancel.current?.focus();
    const el = box.current as HTMLDivElement;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancelRef.current();
    };
    el.addEventListener('keydown', onKey);
    return () => el.removeEventListener('keydown', onKey);
  }, []);
  return (
    <div
      role="alertdialog"
      aria-modal="false"
      aria-labelledby={`${id}-t`}
      aria-describedby={`${id}-d`}
      className="confirm card warning"
      ref={box}
    >
      <p id={`${id}-t`} className="error-title">
        {title}
      </p>
      <div id={`${id}-d`}>{children}</div>
      <div className="row">
        <button type="button" className="danger" onClick={onConfirm} disabled={busy}>
          {busy ? 'Working…' : confirmLabel}
        </button>
        <button type="button" ref={cancel} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
