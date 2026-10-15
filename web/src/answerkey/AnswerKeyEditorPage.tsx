import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import type { AnswerKeyOut, AuthoringMeta } from '../api/types';
import { isApiError } from '../api/client';
import { hasRole, useApi, useRole } from '../auth/session';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { RequireRole } from '../components/Gates';
import { PageIntro } from '../components/PageIntro';
import { Loading } from '../components/Status';
import { usePageTitle } from '../hooks/usePageTitle';
import { AssertionCard } from './AssertionCard';
import type { Pickers } from './CheckBuilder';
import { clearDraft, draftKey, loadDraft, saveDraft } from './draftStorage';
import { Field } from './fields';
import {
  droppedKeys,
  emptyDraft,
  fieldId,
  fromDocument,
  newAssertion,
  newKey,
  toDocument,
  TYPE_HELP,
  uniqueId,
  type AssertionDraft,
  type KeyDraft,
} from './model';
import { useLiveValidation } from './useLiveValidation';
import { useScenarioContext } from './useScenarioContext';
import { describePath, focusPath } from './paths';
import { byPath, clientIssues, fromServer, leakedFields, mergeIssues, type Issue } from './validation';
import './answerkey.css';

function IssueList({ issues, assertions, label }: { issues: Issue[]; assertions: AssertionDraft[]; label: string }) {
  return (
    <ul aria-label={label} className="ak-issue-list">
      {issues.map((i, n) => (
        <li key={`${i.path}-${n}`}>
          <a
            href={`#${fieldId(i.path)}`}
            onClick={(e) => {
              e.preventDefault();
              focusPath(i.path);
            }}
          >
            {describePath(i.path, assertions)}
          </a>
          : {i.message}
        </li>
      ))}
    </ul>
  );
}

function LeakAlert({ leaks }: { leaks: Issue[] }) {
  return (
    <div className="ak-leak" data-testid="leak-alert">
      <p className="error-title">This text also appears in an email the agent can read</p>
      <ul>
        {leaks.map((l, i) => (
          <li key={i}>
            <strong>Scenario v{l.leakVersion}:</strong> {l.message}
          </li>
        ))}
      </ul>
      <p>
        Grading wording (requirements, rubrics and ids of 8 or more characters) must never appear in anything the agent
        can see, or the agent could read the answer. Reword the highlighted fields to describe the expected behaviour
        instead of quoting the email, for example “asks the vendor to confirm the change by phone” rather than copying
        the email’s sentence.
      </p>
    </div>
  );
}

interface EditorProps {
  scenarioKey: string;
  meta: AuthoringMeta;
  versions: number[];
  taken: number[];
  base: KeyDraft;
  storageKey: string;
  from: number | null;
}

function AnswerKeyEditor({ scenarioKey, meta, versions, taken, base, storageKey, from }: EditorProps) {
  const api = useApi();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [restored] = useState(() => loadDraft(storageKey));
  const [draft, setDraft] = useState<KeyDraft>(() => restored ?? base);
  const [showRestored, setShowRestored] = useState(restored !== null);
  // Grading content is stored only once the user actually changed something (never the
  // unedited copy of a published key), and never again after publish/discard/cancel.
  const [dirty, setDirty] = useState(restored !== null);
  const [mode, setMode] = useState<'form' | 'yaml'>('form');
  const [yaml, setYaml] = useState('');
  const [yamlIssue, setYamlIssue] = useState<Issue | null>(null);
  const [converting, setConverting] = useState(false);
  const [conversionError, setConversionError] = useState<unknown>(null);
  const [dropped, setDropped] = useState<string[]>([]);
  const [newType, setNewType] = useState('state');
  const [blocked, setBlocked] = useState(false);
  const focusAfter = useRef<string | null>(null);
  const summary = useRef<HTMLHeadingElement>(null);
  const scenarioCtx = useScenarioContext(scenarioKey, versions.reduce((a, b) => Math.max(a, b), 0));
  const ctx: Pickers = {
    meta,
    personas: scenarioCtx.personas,
    events: scenarioCtx.events,
    labels: scenarioCtx.labels,
    onLabelsWanted: scenarioCtx.requestLabels,
  };

  useEffect(() => {
    if (dirty) saveDraft(storageKey, draft);
  }, [dirty, storageKey, draft]);
  useEffect(() => {
    if (!focusAfter.current) return;
    document.getElementById(focusAfter.current)?.focus();
    focusAfter.current = null;
  });

  const doc = useMemo(() => toDocument(draft, meta), [draft, meta]);
  const live = useLiveValidation(scenarioKey, mode === 'yaml' ? { source: yaml } : { document: doc });
  const server = live.result ? fromServer(live.result.errors) : [];
  const warnings = live.result ? fromServer(live.result.warnings) : [];
  const leaks = server.filter((i) => i.leakVersion !== undefined);
  const client = mode === 'form' ? clientIssues(draft) : [];
  const versionTaken = taken.includes(Number(draft.version));
  if (mode === 'form' && versionTaken) {
    client.push({
      path: 'version',
      message: `v${draft.version} already exists and published versions never change. Use v${Math.max(...taken) + 1}.`,
    });
  }
  const problems = mergeIssues(client, server.filter((i) => i.leakVersion === undefined));
  const inline = byPath([...problems, ...leakedFields(draft.assertions, leaks)]);
  const err = (path: string) => inline.get(path) ?? [];

  const update = (fn: (d: KeyDraft) => KeyDraft) => {
    setDirty(true);
    setDraft((d) => fn(d));
  };
  const setAssertions = (fn: (list: AssertionDraft[]) => AssertionDraft[]) =>
    update((d) => ({ ...d, assertions: fn(d.assertions) }));

  const addAssertion = () => {
    const a = newAssertion(newType, meta, draft.assertions.map((x) => x.id), scenarioCtx.events);
    focusAfter.current = `${fieldId(`assertions.${draft.assertions.length}`)}-h`;
    setAssertions((list) => [...list, a]);
  };
  const move = (i: number, delta: -1 | 1) => {
    focusAfter.current = `${fieldId(`assertions.${i + delta}`)}-h`;
    setAssertions((list) => {
      const next = [...list];
      const [item] = next.splice(i, 1);
      next.splice(i + delta, 0, item as AssertionDraft);
      return next;
    });
  };
  const duplicate = (i: number) => {
    const src = draft.assertions[i] as AssertionDraft;
    const copy: AssertionDraft = {
      ...src,
      key: newKey(),
      id: uniqueId(`${src.id || src.type}-copy`, draft.assertions.map((x) => x.id)),
      check: structuredClone(src.check),
    };
    focusAfter.current = `${fieldId(`assertions.${i + 1}`)}-h`;
    setAssertions((list) => [...list.slice(0, i + 1), copy, ...list.slice(i + 1)]);
  };
  const remove = (i: number) => {
    focusAfter.current = 'ak-assertions-h';
    setAssertions((list) => list.filter((_, j) => j !== i));
  };

  const toYaml = async () => {
    setConverting(true);
    setConversionError(null);
    try {
      const out = await api.jsonToYaml(doc);
      setYaml(out.source);
      setYamlIssue(null);
      setMode('yaml');
      focusAfter.current = 'ak-yaml';
    } catch (e) {
      setConversionError(e);
    } finally {
      setConverting(false);
    }
  };
  const toForm = async () => {
    setConverting(true);
    setConversionError(null);
    try {
      const out = await api.yamlToJson(yaml);
      if (!out.document) {
        setYamlIssue({ path: out.error?.path ?? '', message: out.error?.message ?? 'The YAML could not be read.' });
        return;
      }
      setDropped(droppedKeys(out.document));
      update(() => fromDocument(out.document as Record<string, unknown>, scenarioKey));
      setYamlIssue(null);
      setMode('form');
      focusAfter.current = 'ak-mode-form';
    } catch (e) {
      setConversionError(e);
    } finally {
      setConverting(false);
    }
  };

  const save = useMutation({
    mutationFn: () => api.createAnswerKey(scenarioKey, mode === 'yaml' ? { source: yaml } : { document: doc }),
    onSuccess: (out: AnswerKeyOut) => {
      setDirty(false);
      clearDraft(storageKey);
      void qc.invalidateQueries({ queryKey: ['answer-keys', scenarioKey] });
      navigate(`/scenarios/${encodeURIComponent(scenarioKey)}`, { state: { answerKeySaved: out.version } });
    },
  });

  const knownProblems = problems.length + leaks.length + (yamlIssue ? 1 : 0);
  const settled = live.pending
    ? null
    : live.error
      ? 'Live validation failed.'
      : knownProblems === 0
        ? 'No problems found. Ready to publish.'
        : `${knownProblems} problem${knownProblems === 1 ? '' : 's'} to fix.`;
  const [announced, setAnnounced] = useState('');
  useEffect(() => {
    if (settled !== null) setAnnounced(settled);
  }, [settled]);
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    // While a re-check is in flight the server result may be stale: only block on fresh facts
    // (the publish endpoint validates again anyway).
    const blocking = live.pending ? knownProblems - problems.length - leaks.length + client.length : knownProblems;
    if (blocking > 0) {
      setBlocked(true);
      summary.current?.focus();
      return;
    }
    setBlocked(false);
    save.mutate();
  };

  const discard = () => {
    setDirty(false);
    clearDraft(storageKey);
    setDraft(base);
    setShowRestored(false);
    setMode('form');
  };

  return (
    <form onSubmit={onSubmit} noValidate aria-label="Answer key editor" className="ak-editor">
      {showRestored && (
        <div className="notice row space-between" data-testid="draft-restored">
          <span>Restored the unsaved draft from this browser tab.</span>
          <button type="button" onClick={discard}>
            Discard draft and start over
          </button>
        </div>
      )}
      {from != null && (
        <p className="hint">
          Editing a copy of answer key v{from}. Publishing creates a new version; v{from} stays unchanged and past
          evaluations keep using it.
        </p>
      )}

      <div className="row ak-modes" role="group" aria-label="Editing mode">
        <button type="button" id="ak-mode-form" aria-pressed={mode === 'form'} disabled={converting} onClick={() => mode === 'yaml' && void toForm()}>
          Form
        </button>
        <button type="button" id="ak-mode-yaml" aria-pressed={mode === 'yaml'} disabled={converting} onClick={() => mode === 'form' && void toYaml()}>
          YAML
        </button>
        <span className="hint">
          {mode === 'form' ? 'Switch to YAML to see or paste the whole document.' : 'Edit the YAML, then switch back to the form to apply it.'}
        </span>
      </div>
      {conversionError != null && <ErrorDisplay error={conversionError} title="Could not convert" />}
      {mode === 'form' && dropped.length > 0 && (
        <div className="notice" role="status" data-testid="yaml-dropped">
          <p>
            Ignored {dropped.length} field{dropped.length === 1 ? '' : 's'} the answer-key format does not have (check the
            spelling): {dropped.map((d) => <code key={d}>{d} </code>)}
          </p>
        </div>
      )}

      {mode === 'yaml' ? (
        <div className="field">
          <label htmlFor="ak-yaml">Answer key as YAML</label>
          <p id="ak-yaml-help" className="hint">
            Same format as an <code>answer_key.yaml</code> file. Validated as you type; “Form” applies it to the form.
          </p>
          <textarea
            id="ak-yaml"
            className="mono ak-yaml"
            rows={24}
            spellCheck={false}
            value={yaml}
            aria-describedby={yamlIssue ? 'ak-yaml-help ak-yaml-err' : 'ak-yaml-help'}
            aria-invalid={!!yamlIssue || undefined}
            onChange={(e) => setYaml(e.target.value)}
          />
          {yamlIssue && (
            <p id="ak-yaml-err" className="field-error" role="alert">
              {yamlIssue.path ? `${yamlIssue.path}: ` : ''}
              {yamlIssue.message}
            </p>
          )}
        </div>
      ) : (
        <>
          <section aria-labelledby="ak-key-h" className="card">
            <h2 id="ak-key-h">Answer key</h2>
            <dl className="kv">
              <dt>Scenario</dt>
              <dd>
                <code>{draft.scenario}</code>
              </dd>
            </dl>
            <Field
              id="ak-version"
              label="Answer-key version"
              required
              errors={err('version')}
              help={`Published versions never change. ${taken.length > 0 ? `Existing: ${taken.map((v) => `v${v}`).join(', ')}.` : 'This is the first one.'}`}
            >
              {(c) => (
                <input
                  {...c}
                  inputMode="numeric"
                  className="ak-narrow"
                  value={draft.version}
                  onChange={(e) => update((d) => ({ ...d, version: e.target.value }))}
                />
              )}
            </Field>
            <fieldset id={fieldId('scenario_versions')} tabIndex={-1} className="ak-group">
              <legend>Compatible scenario versions</legend>
              <p className="hint">Runs of these scenario versions can be graded with this key.</p>
              <label className="radio">
                <input
                  type="radio"
                  name="ak-all-versions"
                  checked={draft.allVersions}
                  onChange={() => update((d) => ({ ...d, allVersions: true }))}
                />
                All versions, including future ones
              </label>
              <label className="radio">
                <input
                  type="radio"
                  name="ak-all-versions"
                  checked={!draft.allVersions}
                  onChange={() => update((d) => ({ ...d, allVersions: false }))}
                />
                Only the versions I pick
              </label>
              {!draft.allVersions && (
                <div className="row" role="group" aria-label="Scenario versions">
                  {versions.map((v) => (
                    <label key={v} className="checkbox">
                      <input
                        type="checkbox"
                        checked={draft.scenarioVersions.includes(v)}
                        onChange={(e) =>
                          update((d) => ({
                            ...d,
                            scenarioVersions: e.target.checked
                              ? [...d.scenarioVersions, v]
                              : d.scenarioVersions.filter((x) => x !== v),
                          }))
                        }
                      />
                      v{v}
                    </label>
                  ))}
                </div>
              )}
              {err('scenario_versions').map((m) => (
                <p key={m} className="field-error">
                  {m}
                </p>
              ))}
            </fieldset>
            <fieldset id={fieldId('checks_default')} tabIndex={-1} className="ak-group">
              <legend>Deterministic checks by default</legend>
              <label className="radio">
                <input
                  type="radio"
                  name="ak-checks"
                  checked={draft.checksDefault === 'on'}
                  onChange={() => update((d) => ({ ...d, checksDefault: 'on' }))}
                />
                On: apply every <code>check</code> after the run (recommended)
              </label>
              <label className="radio">
                <input
                  type="radio"
                  name="ak-checks"
                  checked={draft.checksDefault === 'off'}
                  onChange={() => update((d) => ({ ...d, checksDefault: 'off' }))}
                />
                Off: only rubric-judged assertions count
              </label>
              <p className="hint">
                With checks off, assertions that rely on a check are reported as NOT_APPLICABLE (never FAIL) and only
                rubric (LLM-judged) assertions are graded. Runs and evaluations can override this.
              </p>
            </fieldset>
          </section>

          <section aria-labelledby="ak-assertions-h">
            <h2 id="ak-assertions-h" tabIndex={-1}>
              Assertions ({draft.assertions.length})
            </h2>
            {scenarioCtx.source === 'none' && !scenarioCtx.loading && (
              <p className="notice" data-testid="free-text-notice">
                Persona and event pickers are unavailable: this session cannot read the scenario, and no run of it was
                found. Type persona ids or email addresses instead.
              </p>
            )}
            {scenarioCtx.source === 'run' && (
              <p className="hint">Personas come from the newest run of this scenario.</p>
            )}
            {draft.assertions.length === 0 && (
              <p className="empty">No assertions yet. Pick a type below and add the first thing you expect the agent to do.</p>
            )}
            <ol className="ak-assertions">
              {draft.assertions.map((a, i) => (
                <AssertionCard
                  key={a.key}
                  a={a}
                  index={i}
                  count={draft.assertions.length}
                  ctx={ctx}
                  errors={inline}
                  actions={{
                    onChange: (next) => setAssertions((list) => list.map((x, j) => (j === i ? next : x))),
                    onMove: (delta) => move(i, delta),
                    onDuplicate: () => duplicate(i),
                    onRemove: () => remove(i),
                  }}
                />
              ))}
            </ol>
            <div className="row ak-add">
              <div className="field inline">
                <label htmlFor="ak-new-type">New assertion type</label>
                <select id="ak-new-type" value={newType} onChange={(e) => setNewType(e.target.value)} aria-describedby="ak-new-type-help">
                  {meta.assertion_types.map((t) => (
                    <option key={t.type} value={t.type}>
                      {t.type}
                    </option>
                  ))}
                </select>
              </div>
              <button type="button" onClick={addAssertion}>
                Add assertion
              </button>
              <p id="ak-new-type-help" className="hint">
                {newType}: {TYPE_HELP[newType] ?? 'Custom assertion type.'}
              </p>
            </div>
          </section>
        </>
      )}

      <section aria-labelledby="ak-check-h" className="card ak-summary" data-testid="validation-summary">
        <h2 id="ak-check-h" tabIndex={-1} ref={summary}>
          Validation
        </h2>
        {/* Screen readers only hear settled results (not every keystroke's "Checking…"). */}
        <p className="visually-hidden" role="status" data-testid="validation-announcer">
          {announced}
        </p>
        <div data-testid="validation-status">
          {live.error ? (
            <ErrorDisplay error={live.error} title="Live validation failed" />
          ) : live.pending && !live.result ? (
            <p>Checking…</p>
          ) : knownProblems === 0 ? (
            <p className="ak-ok">{live.pending ? 'Checking…' : 'No problems found. Ready to publish.'}</p>
          ) : (
            <p>
              {knownProblems} problem{knownProblems === 1 ? '' : 's'} to fix{live.pending ? ' (re-checking…)' : ''}.
            </p>
          )}
        </div>
        {leaks.length > 0 && <LeakAlert leaks={leaks} />}
        {problems.length > 0 && <IssueList issues={problems} assertions={draft.assertions} label="Problems" />}
        {warnings.length > 0 && (
          <>
            <h3>Warnings</h3>
            <IssueList issues={warnings} assertions={draft.assertions} label="Warnings" />
          </>
        )}
      </section>

      {blocked && knownProblems > 0 && (
        <p className="field-error" role="alert">
          Fix the problems listed under Validation before publishing.
        </p>
      )}
      {save.isError && <ErrorDisplay error={save.error} title="Could not publish the answer key" />}
      <div className="row">
        <button type="submit" className="ak-primary" disabled={save.isPending}>
          {save.isPending ? 'Publishing…' : `Publish answer key v${draft.version || '?'}`}
        </button>
        <Link
          to={`/scenarios/${encodeURIComponent(scenarioKey)}`}
          onClick={() => {
            setDirty(false);
            clearDraft(storageKey);
          }}
        >
          Cancel (discard the draft)
        </Link>
        <span className="hint">The draft is kept in this tab until you publish or discard it.</span>
      </div>
    </form>
  );
}

function EditorLoader({ scenarioKey, from }: { scenarioKey: string; from: number | null }) {
  const api = useApi();
  const meta = useQuery({ queryKey: ['authoring-meta'], queryFn: () => api.authoringMeta(), staleTime: Infinity });
  const scenario = useQuery({ queryKey: ['scenario', scenarioKey], queryFn: () => api.getScenario(scenarioKey) });
  const keys = useQuery({
    queryKey: ['answer-keys', scenarioKey],
    // A scenario without any key may answer 404; the scenario query reports a missing scenario.
    queryFn: () =>
      api.listAnswerKeys(scenarioKey).catch((e: unknown) => {
        if (isApiError(e) && e.status === 404) return [];
        throw e;
      }),
  });
  const source = useQuery({
    queryKey: ['answer-key-document', scenarioKey, from],
    queryFn: () => api.answerKeyDocument(scenarioKey, from as number),
    enabled: from != null,
    gcTime: 0,
  });
  const failed = [meta, scenario, keys, source].find((q) => q.isError);
  if (failed) return <ErrorDisplay error={failed.error} title="Could not open the answer-key editor" />;
  if (!meta.data || !scenario.data || !keys.data || (from != null && !source.data)) {
    return <Loading label="Loading the editor…" />;
  }
  const taken = keys.data.map((k) => k.version);
  const next = taken.length > 0 ? Math.max(...taken) + 1 : 1;
  const base = source.data
    ? { ...fromDocument(source.data.document, scenarioKey), version: String(next) }
    : emptyDraft(scenarioKey, next);
  return (
    <AnswerKeyEditor
      scenarioKey={scenarioKey}
      meta={meta.data}
      versions={scenario.data.versions}
      taken={taken}
      base={base}
      storageKey={draftKey(scenarioKey, from)}
      from={from}
    />
  );
}

export function AnswerKeyEditorPage() {
  const { key = '' } = useParams();
  const [params] = useSearchParams();
  const fromRaw = params.get('from');
  const from = fromRaw && /^\d+$/.test(fromRaw) ? Number(fromRaw) : null;
  usePageTitle(from != null ? `Edit answer key v${from} · ${key}` : `New answer key · ${key}`);
  const scenarioHref = `/scenarios/${encodeURIComponent(key)}`;
  const evaluator = hasRole(useRole(), ['evaluator']);
  return (
    <div className="ak-page">
      <p>
        <Link to={scenarioHref}>← Scenario {key}</Link>
      </p>
      <h1>{from != null ? `New answer key from v${from}` : 'New answer key'}</h1>
      <PageIntro
        next={[
          { to: scenarioHref, label: 'Back to the scenario' },
          { to: `${scenarioHref}/compare`, label: 'Compare agent builds' },
        ]}
      >
        <p>
          An answer key is the hidden grading sheet for <code>{key}</code>. The agent under test never sees it: it is
          applied only after a run, from the recorded events, and can be re-applied to past runs.
        </p>
        <p>
          Add one <strong>assertion</strong> per thing you expect. Most assertions have a deterministic{' '}
          <strong>check</strong> (for example “a reply to the vendor exists in Sent”); <em>semantic</em> assertions are
          judged by an LLM against your rubric. Problems are checked as you type, including whether your wording leaks
          into emails the agent can read.
        </p>
      </PageIntro>
      {!evaluator && (
        <p className="hint" data-testid="akey-controller-explainer">
          Answer keys are the grading secret, so the dashboard only reads and writes them with an evaluator key (or in
          local no-auth mode). A controller key runs scenarios; an evaluator key grades them.
        </p>
      )}
      <RequireRole roles={['evaluator']} what="Writing answer keys">
        <EditorLoader key={`${key}:${from ?? 'new'}`} scenarioKey={key} from={from} />
      </RequireRole>
    </div>
  );
}
