// YAML view of the form. The browser has no YAML library: the server renders the form's
// document (json-to-yaml) and parses edits (yaml-to-json). Edits only reach the form when the
// user applies them and they parse; a parse error is shown here and the form is untouched.
import { useQuery } from '@tanstack/react-query';
import { useId, useState } from 'react';
import type { ValidationIssue } from '../api/types';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { useBuilder } from './context';
import { fromDocument, toDocument, type Imported } from './model';
import { LIVE_DEBOUNCE_MS, useDebounced } from './useDebounced';

export function YamlPanel({ onApply }: { onApply: (imported: Imported) => void }) {
  const { api, draft } = useBuilder();
  const id = useId();
  const docKey = useDebounced(JSON.stringify(toDocument(draft.form)), LIVE_DEBOUNCE_MS);
  const [edited, setEdited] = useState<string | null>(null);
  const [parseError, setParseError] = useState<ValidationIssue | null>(null);
  const [applyError, setApplyError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const rendered = useQuery({
    queryKey: ['builder-yaml', docKey],
    queryFn: () => api.builder.jsonToYaml(JSON.parse(docKey) as Record<string, unknown>),
    staleTime: Infinity,
    retry: false,
  });
  const text = edited ?? rendered.data?.source ?? '';

  const apply = async () => {
    if (edited === null) return;
    setBusy(true);
    setApplyError(null);
    try {
      const res = await api.builder.yamlToJson(edited);
      if (res.error || !res.document) {
        setParseError(res.error ?? { path: '<root>', message: 'The text is not a scenario document.' });
      } else {
        setParseError(null);
        setEdited(null);
        onApply(fromDocument(res.document));
      }
    } catch (err) {
      setApplyError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="b-yaml" aria-labelledby={`${id}-h`} data-testid="yaml-panel">
      <h2 id={`${id}-h`}>YAML</h2>
      <p className="hint" id={`${id}-help`}>
        The same scenario as text. Edit it here and choose “Apply YAML to form”; the form changes only if the YAML
        parses. Fixture files stay in the form.
      </p>
      {rendered.isError && <ErrorDisplay error={rendered.error} title="Could not render YAML" />}
      <label htmlFor={`${id}-text`} className="visually-hidden">
        Scenario YAML
      </label>
      <textarea
        id={`${id}-text`}
        className="b-yaml-text"
        spellCheck={false}
        rows={30}
        value={text}
        aria-invalid={parseError ? true : undefined}
        aria-describedby={parseError ? `${id}-help ${id}-err` : `${id}-help`}
        onChange={(e) => setEdited(e.target.value)}
      />
      {parseError && (
        <p className="field-error" role="alert" id={`${id}-err`} data-testid="yaml-error">
          YAML not applied — {parseError.path}: {parseError.message}
        </p>
      )}
      {applyError !== null && <ErrorDisplay error={applyError} title="Could not read the YAML" />}
      <div className="row">
        <button type="button" disabled={edited === null || busy} onClick={() => void apply()}>
          {busy ? 'Applying…' : 'Apply YAML to form'}
        </button>
        <button
          type="button"
          disabled={edited === null}
          onClick={() => {
            setEdited(null);
            setParseError(null);
          }}
        >
          Discard YAML edits
        </button>
        {edited !== null && <span className="muted">Unapplied YAML edits</span>}
      </div>
    </section>
  );
}
