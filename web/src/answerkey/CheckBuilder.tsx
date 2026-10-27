// Structured editor for an assertion's `check:` block, generated from the type's JSON Schema
// (meta.assertion_types[].check_schema). Known fields get pickers (personas, folders, flags,
// operations, invariants, scenario events, counts); anything else falls back to a generic
// control chosen by its schema kind, so every assertion type is editable.
import { useState } from 'react';
import type { AuthoringMeta } from '../api/types';
import { Field, GroupErrors, type ControlProps } from './fields';
import { GROUP_LABELS, HELP, INVARIANT_HELP } from './help';
import { fieldId, pathKey, setIn, type Path } from './model';
import { itemsOf, kindOf, labelFor, properties, requiredKeys, unwrap, type JsonSchema } from './schema';

export interface PersonaOption {
  id: string;
  email: string;
  /** "Asha Rao, Finance Manager" */
  label: string;
}

export interface Pickers {
  meta: AuthoringMeta;
  personas: PersonaOption[];
  /** Scenario event ids (for temporal checks and evaluate-at). */
  events: string[];
  /** Labels seen in the scenario/run, offered for `label`. */
  labels?: string[];
  /** Server-side defaults of groups left out, by property name (e.g. where -> "folder: sent"). */
  notes?: Record<string, string>;
  /** Meaning of an explicitly empty group ({}), by property name. */
  emptyNotes?: Record<string, string>;
  /** Called when the label picker is first used (suggestions are fetched lazily). */
  onLabelsWanted?: () => void;
}

interface NodeProps {
  root: JsonSchema;
  schema: JsonSchema;
  name: string;
  path: Path;
  value: unknown;
  required: boolean;
  onChange: (v: unknown) => void;
  ctx: Pickers;
  errors: Map<string, string[]>;
  /** Schema default of a list (e.g. protocol error_codes), shown when nothing is set. */
  defaultValue?: unknown;
}

const asObj = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const asStr = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');
/** '' clears an optional value so the server default applies. */
const orUndef = (v: string) => (v === '' ? undefined : v);

function Choice({
  control,
  value,
  onChange,
  options,
  empty,
}: {
  control: ControlProps;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  empty?: string;
}) {
  const known = options.some((o) => o.value === value) || (empty !== undefined && value === '');
  return (
    <select {...control} value={value} onChange={(e) => onChange(e.target.value)}>
      {empty !== undefined && <option value="">{empty}</option>}
      {!known && <option value={value}>{value} (custom)</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function CountField({ p, label, help, errors }: { p: NodeProps; label: string; help?: string; errors: string[] }) {
  const id = fieldId(p.path);
  const v = asObj(p.value);
  // The mode is local so clearing a number does not switch the mode under the user.
  const [mode, setMode] = useState(
    v.eq != null ? 'eq' : v.min != null && v.max != null ? 'range' : v.min != null ? 'min' : v.max != null ? 'max' : '',
  );
  const num = (x: unknown) => asStr(x);
  const set = (next: Record<string, unknown> | undefined) => p.onChange(next);
  /** Whole numbers only; an empty box removes the bound. */
  const n = (s: string) => (s.trim() === '' ? undefined : Math.max(0, Math.trunc(Number(s))));
  const changeMode = (m: string) => {
    setMode(m);
    const seed = Number(v.eq ?? v.min ?? v.max ?? 1);
    if (m === '') set(undefined);
    else if (m === 'range') set({ min: seed, max: seed });
    else set({ [m]: seed });
  };
  const errs = p.errors;
  const numberInput = (key: 'eq' | 'min' | 'max', text: string) => (
    <Field id={fieldId([...p.path, key])} label={text} errors={errs.get(pathKey([...p.path, key])) ?? []}>
      {(c) => (
        <input
          {...c}
          type="number"
          min={0}
          step={1}
          className="ak-narrow"
          value={num(v[key])}
          onChange={(e) => set(setIn(v, [key], n(e.target.value)))}
        />
      )}
    </Field>
  );
  return (
    <fieldset className="ak-group" id={id} tabIndex={-1}>
      <legend>{label}</legend>
      {help && <p className="hint ak-help">{help}</p>}
      <GroupErrors id={id} errors={errors} />
      <div className="ak-row">
        <Field id={`${id}-mode`} label="Expected number">
          {(c) => (
            <select {...c} value={mode} onChange={(e) => changeMode(e.target.value)}>
              <option value="">Default (at least one)</option>
              <option value="eq">Exactly</option>
              <option value="min">At least</option>
              <option value="max">At most</option>
              <option value="range">Between</option>
            </select>
          )}
        </Field>
        {mode === 'eq' && numberInput('eq', 'Exactly')}
        {(mode === 'min' || mode === 'range') && numberInput('min', 'At least')}
        {(mode === 'max' || mode === 'range') && numberInput('max', 'At most')}
      </div>
    </fieldset>
  );
}

function OutcomeField({ control, value, onChange }: { control: ControlProps; value: string; onChange: (v: string | undefined) => void }) {
  const [mode, setMode] = useState(value === '' || value === 'ok' ? 'ok' : value === 'any' ? 'any' : 'code');
  const pick = (m: string) => {
    setMode(m);
    onChange(m === 'ok' ? undefined : m === 'any' ? 'any' : 'PERMISSION_DENIED');
  };
  return (
    <div className="ak-row">
      <select {...control} value={mode} onChange={(e) => pick(e.target.value)}>
        <option value="ok">Succeeded (ok)</option>
        <option value="any">Any outcome</option>
        <option value="code">Failed with error code…</option>
      </select>
      {mode === 'code' && (
        <input
          id={`${control.id}-code`}
          aria-label="Error code"
          className="mono"
          value={value}
          onChange={(e) => onChange(e.target.value || undefined)}
        />
      )}
    </div>
  );
}

function StringList({ p, label, help, errors }: { p: NodeProps; label: string; help?: string; errors: string[] }) {
  const id = fieldId(p.path);
  const items = Array.isArray(p.value) ? p.value.map(asStr) : [];
  // An emptied list is dropped so the server default applies again.
  const set = (next: string[]) => p.onChange(next.length > 0 ? next : undefined);
  // Pydantic omits factory defaults from the schema; the type's notes fill the gap.
  const defaultText = Array.isArray(p.defaultValue) ? (p.defaultValue as unknown[]).map(asStr).join(', ') : p.ctx.notes?.[p.name];
  return (
    <fieldset className="ak-group" id={id} tabIndex={-1}>
      <legend>{label}</legend>
      {help && <p className="hint ak-help">{help}</p>}
      <GroupErrors id={id} errors={errors} />
      {items.length === 0 && (
        <p className="muted">{defaultText ? `Not set: the default is ${defaultText}.` : 'None yet.'}</p>
      )}
      <ul className="ak-list">
        {items.map((item, i) => (
          <li key={i} className="ak-row">
            <Field
              id={fieldId([...p.path, i])}
              label={`${label} ${i + 1}`}
              errors={p.errors.get(pathKey([...p.path, i])) ?? []}
            >
              {(c) => <input {...c} value={item} onChange={(e) => set(items.map((x, j) => (j === i ? e.target.value : x)))} />}
            </Field>
            <button type="button" onClick={() => set(items.filter((_, j) => j !== i))} aria-label={`Remove ${label.toLowerCase()} ${i + 1}`}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => set([...items, ''])}>
        Add to {label.toLowerCase()}
      </button>
    </fieldset>
  );
}

function JsonField({ control, value, onChange }: { control: ControlProps; value: unknown; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(() => (value === undefined ? '' : JSON.stringify(value, null, 2)));
  const [error, setError] = useState('');
  const commit = (t: string) => {
    setText(t);
    if (t.trim() === '') {
      setError('');
      onChange(undefined);
      return;
    }
    try {
      onChange(JSON.parse(t));
      setError('');
    } catch {
      setError('Not valid JSON yet.');
    }
  };
  return (
    <>
      <textarea
        {...control}
        aria-invalid={control['aria-invalid'] || !!error || undefined}
        aria-describedby={[control['aria-describedby'], error ? `${control.id}-json-err` : ''].filter(Boolean).join(' ') || undefined}
        className="mono"
        rows={3}
        value={text}
        onChange={(e) => commit(e.target.value)}
      />
      {error && (
        <p id={`${control.id}-json-err`} className="field-error">
          {error}
        </p>
      )}
    </>
  );
}

function Node(p: NodeProps) {
  const { schema, optional, name: ref } = unwrap(p.schema, p.root);
  const id = fieldId(p.path);
  const errors = p.errors.get(pathKey(p.path)) ?? [];
  const label = GROUP_LABELS[p.name] ?? (ref ? labelFor(p.name, {}) : labelFor(p.name, schema));
  const help = HELP[p.name] ?? (typeof schema.description === 'string' ? schema.description : undefined);
  const kind = kindOf(schema);
  const { meta, personas, events } = p.ctx;

  if (ref === 'Count') return <CountField p={p} label={label} help={help} errors={errors} />;

  if (kind === 'object') {
    // An absent group and an explicit {} mean different things to the server (e.g. content
    // `where` absent = Sent folder, {} = any folder): keep them apart and say which applies.
    const absentNote = p.ctx.notes?.[p.name];
    const emptyNote = p.ctx.emptyNotes?.[p.name];
    const present = p.value != null && typeof p.value === 'object';
    const empty = present && Object.keys(asObj(p.value)).length === 0;
    const useDefault = !present && !optional && !!absentNote;
    const shown = present || (!optional && !absentNote);
    return (
      <fieldset className="ak-group" id={id} tabIndex={-1}>
        <legend>{label}</legend>
        {help && <p className="hint ak-help">{help}</p>}
        {optional && (
          <label className="checkbox">
            <input type="checkbox" checked={present} onChange={(e) => p.onChange(e.target.checked ? {} : undefined)} />
            Use “{label.toLowerCase()}”
          </label>
        )}
        <GroupErrors id={id} errors={errors} />
        {!present && absentNote && (
          <p className="hint" data-testid="group-default">
            Not set: {absentNote}.
          </p>
        )}
        {useDefault && (
          <button type="button" onClick={() => p.onChange({})}>
            Set {label.toLowerCase()} filters instead
          </button>
        )}
        {empty && emptyNote && (
          <p className="hint" data-testid="group-empty">
            {label}: {emptyNote}.
          </p>
        )}
        {present && !optional && absentNote && (
          <button type="button" onClick={() => p.onChange(undefined)}>
            Use the default instead
          </button>
        )}
        {shown && (
          <ObjectFields
            root={p.root}
            schema={schema}
            path={p.path}
            value={asObj(p.value)}
            onChange={(v) => p.onChange(v)}
            ctx={p.ctx}
            errors={p.errors}
          />
        )}
      </fieldset>
    );
  }

  if (kind === 'array') {
    if (kindOf(itemsOf(schema, p.root)) === 'string') {
      return <StringList p={{ ...p, defaultValue: schema.default }} label={label} help={help} errors={errors} />;
    }
  }

  const value = asStr(p.value);
  const setStr = (v: string) => p.onChange(orUndef(v));
  const optionalText = !p.required;
  let control: (c: ControlProps) => React.ReactNode;

  if (kind === 'string' && p.name === 'mailbox') {
    control = (c) =>
      personas.length > 0 ? (
        <Choice
          control={c}
          value={value}
          onChange={setStr}
          empty="The agent’s mailbox (default)"
          options={personas.map((x) => ({ value: x.id, label: `${x.label} — ${x.email} (${x.id})` }))}
        />
      ) : (
        <input {...c} value={value} placeholder="persona id" onChange={(e) => setStr(e.target.value)} />
      );
  } else if (kind === 'string' && p.name === 'folder') {
    control = (c) => (
      <Choice control={c} value={value} onChange={setStr} empty="Any folder" options={meta.folders.map((f) => ({ value: f, label: f }))} />
    );
  } else if (kind === 'string' && (p.name === 'has_flag' || p.name === 'lacks_flag')) {
    control = (c) => (
      <Choice control={c} value={value} onChange={setStr} empty="Not set" options={meta.flags.map((f) => ({ value: f, label: f }))} />
    );
  } else if (kind === 'string' && p.name === 'operation') {
    control = (c) => (
      <Choice
        control={c}
        value={value}
        onChange={setStr}
        empty="Choose an operation"
        options={(meta.graded_operations ?? meta.agent_operations).map((o) => ({ value: o, label: o }))}
      />
    );
  } else if (kind === 'string' && p.name === 'outcome') {
    control = (c) => <OutcomeField control={c} value={value} onChange={(v) => p.onChange(v)} />;
  } else if (kind === 'string' && (p.name === 'from' || p.name === 'to' || p.name === 'event' || p.name === 'label')) {
    const options =
      p.name === 'label'
        ? (p.ctx.labels ?? []).map((l) => ({ value: l, label: l }))
        : p.name === 'event' ? events.map((e) => ({ value: e, label: e })) : personas.filter((x) => x.email).map((x) => ({ value: x.email, label: `${x.label} (${x.id})` }));
    control = (c) => (
      <>
        <input
          {...c}
          list={`${id}-options`}
          value={value}
          onChange={(e) => setStr(e.target.value)}
          onFocus={p.name === 'label' ? p.ctx.onLabelsWanted : undefined}
          autoComplete="off"
        />
        <datalist id={`${id}-options`}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </datalist>
      </>
    );
  } else if (kind === 'enum') {
    const values = (schema.enum as unknown[]).map(String);
    const def = schema.default != null ? String(schema.default) : undefined;
    control = (c) => (
      <Choice
        control={c}
        value={value}
        onChange={setStr}
        empty={def !== undefined ? `Default (${def})` : optionalText ? 'Not set' : 'Choose…'}
        options={values.map((v) => ({ value: v, label: p.name === 'invariant' && INVARIANT_HELP[v] ? `${v} — ${INVARIANT_HELP[v]}` : v }))}
      />
    );
  } else if (kind === 'boolean') {
    if (optional) {
      control = (c) => (
        <select
          {...c}
          value={p.value === true ? 'yes' : p.value === false ? 'no' : ''}
          onChange={(e) => p.onChange(e.target.value === '' ? undefined : e.target.value === 'yes')}
        >
          <option value="">Either</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      );
    } else {
      const checked = p.value === undefined ? schema.default === true : p.value === true;
      return (
        <div className="field ak-field">
          <label className="checkbox">
            <input
              type="checkbox"
              id={id}
              checked={checked}
              aria-describedby={help ? `${id}-help` : undefined}
              onChange={(e) => p.onChange(e.target.checked === (schema.default === true) ? undefined : e.target.checked)}
            />
            {label}
          </label>
          {help && (
            <p id={`${id}-help`} className="hint ak-help">
              {help}
            </p>
          )}
          <GroupErrors id={id} errors={errors} />
        </div>
      );
    }
  } else if (kind === 'integer' || kind === 'number') {
    control = (c) => (
      <input
        {...c}
        type="number"
        min={typeof schema.minimum === 'number' ? schema.minimum : undefined}
        step={kind === 'integer' ? 1 : 'any'}
        className="ak-narrow"
        value={value}
        placeholder={schema.default != null ? String(schema.default) : undefined}
        onChange={(e) => p.onChange(e.target.value === '' ? undefined : Number(e.target.value))}
      />
    );
  } else if (kind === 'string') {
    control = (c) => (
      <input
        {...c}
        className={p.name === 'regex' ? 'mono' : undefined}
        value={value}
        placeholder={schema.default != null ? String(schema.default) : undefined}
        onChange={(e) => setStr(e.target.value)}
      />
    );
  } else {
    control = (c) => <JsonField control={c} value={p.value} onChange={p.onChange} />;
  }

  return (
    <Field id={id} label={label} help={help} errors={errors} required={p.required}>
      {control}
    </Field>
  );
}

interface ObjectProps {
  root: JsonSchema;
  schema: JsonSchema;
  path: Path;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  ctx: Pickers;
  errors: Map<string, string[]>;
}

function ObjectFields(p: ObjectProps) {
  const req = requiredKeys(p.schema);
  return (
    <div className="ak-fields">
      {properties(p.schema).map(([k, s]) => (
        <Node
          key={k}
          root={p.root}
          schema={s}
          name={k}
          path={[...p.path, k]}
          value={p.value[k]}
          required={req.includes(k)}
          onChange={(v) => p.onChange(setIn(p.value, [k], v))}
          ctx={p.ctx}
          errors={p.errors}
        />
      ))}
    </div>
  );
}

/** The whole `check:` block of one assertion. `path` is the form path of the check. */
export function CheckBuilder({
  schema,
  value,
  onChange,
  path,
  ctx,
  errors,
}: {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  path: Path;
  ctx: Pickers;
  errors: Map<string, string[]>;
}) {
  const id = fieldId(path);
  return (
    <div id={id} tabIndex={-1} className="ak-check">
      <GroupErrors id={id} errors={errors.get(pathKey(path)) ?? []} />
      <ObjectFields root={schema} schema={unwrap(schema, schema).schema} path={path} value={value} onChange={onChange} ctx={ctx} errors={errors} />
    </div>
  );
}
