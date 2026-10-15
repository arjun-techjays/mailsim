// Small accessible form primitives shared by the answer-key editor.
import type { ReactNode } from 'react';

export interface ControlProps {
  id: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean;
}

/** Label + help + inline errors around one control. `children` receives the control's a11y props. */
export function Field({
  id,
  label,
  help,
  errors = [],
  required = false,
  children,
}: {
  id: string;
  label: string;
  help?: ReactNode;
  errors?: string[];
  required?: boolean;
  children: (props: ControlProps) => ReactNode;
}) {
  const helpId = help ? `${id}-help` : undefined;
  const errId = errors.length > 0 ? `${id}-err` : undefined;
  const describedBy = [helpId, errId].filter(Boolean).join(' ') || undefined;
  return (
    <div className="field ak-field">
      <label htmlFor={id}>
        {label}
        {required && <span className="ak-required"> (required)</span>}
      </label>
      {help && (
        <p id={helpId} className="hint ak-help">
          {help}
        </p>
      )}
      {children({ id, 'aria-describedby': describedBy, 'aria-invalid': errors.length > 0 || undefined })}
      {errId && (
        <ul id={errId} className="field-error ak-errors">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Errors attached to a group (fieldset) rather than a single control. */
export function GroupErrors({ id, errors }: { id: string; errors: string[] }) {
  if (errors.length === 0) return null;
  return (
    <ul id={`${id}-err`} className="field-error ak-errors">
      {errors.map((e) => (
        <li key={e}>{e}</li>
      ))}
    </ul>
  );
}
