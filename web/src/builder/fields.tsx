// Labelled form controls bound to a document path: each shows its help text and the live
// validation issues reported at that path, and carries the id the error summary focuses.
import { useId, useState, type ReactNode } from 'react';
import { useIssues } from './context';
import { fieldId } from './paths';

interface Described {
  describedBy: string | undefined;
  invalid: boolean;
  extras: ReactNode;
}

/** Help + inline error elements for a control, and the matching aria attributes. */
function useDescribed(path: string, help?: ReactNode): Described {
  const base = useId();
  const issues = useIssues(path);
  const ids: string[] = [];
  if (help) ids.push(`${base}-help`);
  if (issues.length > 0) ids.push(`${base}-err`);
  return {
    describedBy: ids.length > 0 ? ids.join(' ') : undefined,
    invalid: issues.length > 0,
    extras: (
      <>
        {help && (
          <span id={`${base}-help`} className="hint b-help">
            {help}
          </span>
        )}
        {issues.length > 0 && (
          <span id={`${base}-err`} className="field-error" data-testid="inline-error">
            {issues.join('; ')}
          </span>
        )}
      </>
    ),
  };
}

interface TextProps {
  path: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  help?: ReactNode;
  type?: 'text' | 'email' | 'number' | 'datetime-local';
  readOnly?: boolean;
  placeholder?: string;
  required?: boolean;
  min?: number;
}

export function TextField({ path, label, value, onChange, help, type = 'text', readOnly, placeholder, required, min }: TextProps) {
  const d = useDescribed(path, help);
  const id = fieldId(path);
  return (
    <div className="field">
      <label htmlFor={id}>
        {label}
        {required && <span aria-hidden="true"> *</span>}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        min={min}
        readOnly={readOnly}
        required={required}
        placeholder={placeholder}
        aria-invalid={d.invalid || undefined}
        aria-describedby={d.describedBy}
        onChange={(e) => onChange(e.target.value)}
      />
      {d.extras}
    </div>
  );
}

export function TextArea({ path, label, value, onChange, help, rows = 4 }: Omit<TextProps, 'type'> & { rows?: number }) {
  const d = useDescribed(path, help);
  const id = fieldId(path);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea
        id={id}
        rows={rows}
        value={value}
        aria-invalid={d.invalid || undefined}
        aria-describedby={d.describedBy}
        onChange={(e) => onChange(e.target.value)}
      />
      {d.extras}
    </div>
  );
}

export interface Option {
  value: string;
  label: string;
}

export function SelectField({
  path,
  label,
  value,
  onChange,
  options,
  help,
  placeholder,
}: {
  path: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Option[];
  help?: ReactNode;
  placeholder?: string;
}) {
  const d = useDescribed(path, help);
  const id = fieldId(path);
  const known = options.some((o) => o.value === value);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        value={value}
        aria-invalid={d.invalid || undefined}
        aria-describedby={d.describedBy}
        onChange={(e) => onChange(e.target.value)}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {!known && value !== '' && <option value={value}>{value} (unknown)</option>}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {d.extras}
    </div>
  );
}

/** A group of controls (fieldset) registered under `path`, showing issues reported at it. */
export function Group({ path, legend, help, children, className }: { path: string; legend: ReactNode; help?: ReactNode; children: ReactNode; className?: string }) {
  const d = useDescribed(path, help);
  return (
    <fieldset id={fieldId(path)} tabIndex={-1} className={className} aria-describedby={d.describedBy}>
      <legend>{legend}</legend>
      {d.extras}
      {children}
    </fieldset>
  );
}

/** Editable list of short strings (tags, knowledge items). */
export function StringListField({
  path,
  label,
  items,
  onChange,
  help,
  placeholder,
}: {
  path: string;
  label: string;
  items: string[];
  onChange: (items: string[]) => void;
  help?: ReactNode;
  placeholder?: string;
}) {
  const [text, setText] = useState('');
  const id = fieldId(path);
  const d = useDescribed(path, help);
  const add = () => {
    const v = text.trim();
    if (v && !items.includes(v)) onChange([...items, v]);
    setText('');
  };
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {items.length > 0 && (
        <ul className="b-chips" aria-label={`${label}: current items`}>
          {items.map((item, i) => (
            <li key={item}>
              <span>{item}</span>
              <button type="button" className="b-chip-remove" aria-label={`Remove ${item}`} onClick={() => onChange(items.filter((_, j) => j !== i))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="row b-tight">
        <input
          id={id}
          value={text}
          placeholder={placeholder}
          aria-invalid={d.invalid || undefined}
          aria-describedby={d.describedBy}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" onClick={add} aria-label={`Add to ${label}`}>
          Add
        </button>
      </div>
      {d.extras}
    </div>
  );
}

/** Checkbox list of choices (personas, operations) with optional per-item help. */
export function CheckboxGroup({
  path,
  legend,
  options,
  selected,
  onChange,
  help,
}: {
  path: string;
  legend: string;
  options: (Option & { help?: string })[];
  selected: string[];
  onChange: (next: string[]) => void;
  help?: ReactNode;
}) {
  const base = useId();
  return (
    <Group path={path} legend={legend} help={help} className="b-checks">
      {options.length === 0 && <p className="muted">Nothing to choose from yet.</p>}
      {options.map((o, i) => (
        <div className="checkbox" key={o.value}>
          <input
            id={`${base}-${i}`}
            type="checkbox"
            checked={selected.includes(o.value)}
            onChange={(e) =>
              onChange(e.target.checked ? [...selected, o.value] : selected.filter((v) => v !== o.value))
            }
          />
          <label htmlFor={`${base}-${i}`}>
            {o.label}
            {o.help && <span className="hint"> — {o.help}</span>}
          </label>
        </div>
      ))}
    </Group>
  );
}

/** Radio choice with optional per-option help. */
export function RadioGroup({
  path,
  legend,
  options,
  value,
  onChange,
  help,
}: {
  path: string;
  legend: string;
  options: (Option & { help?: string })[];
  value: string;
  onChange: (v: string) => void;
  help?: ReactNode;
}) {
  const base = useId();
  return (
    <Group path={path} legend={legend} help={help} className="b-checks">
      {options.map((o, i) => (
        <div className="radio" key={o.value}>
          <input id={`${base}-${i}`} type="radio" name={base} checked={value === o.value} onChange={() => onChange(o.value)} />
          <label htmlFor={`${base}-${i}`}>
            {o.label}
            {o.help && <span className="hint"> — {o.help}</span>}
          </label>
        </div>
      ))}
    </Group>
  );
}

