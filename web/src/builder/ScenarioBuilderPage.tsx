// Scenario builder: /scenarios/new, /scenarios/:key/edit?from=<v>, /scenarios/:key/clone?from=<v>.
// A sectioned form over the scenario document with live server-side validation, a YAML view,
// .eml composition/upload for initial messages, and an unsaved-draft safety net.
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useApi } from '../auth/session';
import { RequireRole } from '../components/Gates';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { PageIntro } from '../components/PageIntro';
import { Loading } from '../components/Status';
import { usePageTitle } from '../hooks/usePageTitle';
import './builder.css';
import { BuilderContext, type BuilderValue } from './context';
import { clearDraft, draftStorageKey, loadDraft, saveDraft, type BuilderMode, type Draft, type FixtureInfo } from './draft';
import { FALLBACK_META, type BuilderMeta } from './help';
import { LeaveGuard } from './LeaveGuard';
import { blankForm, documentKey, fromDocument, referencedFixtures, toDocument, type Imported, type ScenarioForm } from './model';
import { findTarget, indexIssues, SECTIONS, type IndexedIssue, type SectionId } from './paths';
import { AgentSection } from './sections/Agent';
import { BasicsSection } from './sections/Basics';
import { EventsSection } from './sections/Events';
import { LimitsSection } from './sections/Limits';
import { MailboxesSection } from './sections/Mailboxes';
import { MessagesSection } from './sections/Messages';
import { OrganisationSection } from './sections/Organisation';
import { PersonasSection } from './sections/Personas';
import { templateDraft } from './template';
import { useDebounced, LIVE_DEBOUNCE_MS } from './useDebounced';
import { ErrorSummary, StatusBadge, type ValidationState } from './Validation';
import { YamlPanel } from './YamlPanel';

interface Loaded {
  draft: Draft;
  baseline: string;
}

const PERSIST_MS = 300;

/** Fixtures referenced by the form (only these are validated and published). */
function usedFixtures(draft: Draft): Record<string, string> {
  const out: Record<string, string> = {};
  for (const path of referencedFixtures(draft.form)) {
    const b64 = draft.fixtures[path];
    if (b64 !== undefined) out[path] = b64;
  }
  return out;
}

function fixturesSignature(fixtures: Record<string, string>): string {
  return Object.entries(fixtures)
    .map(([p, b]) => `${p}:${b.length}:${b.slice(-16)}`)
    .join('|');
}

function Chooser({ onPick }: { onPick: (l: Loaded) => void }) {
  const pick = (draft: Draft) => onPick({ draft, baseline: documentKey(draft.form) });
  return (
    <section aria-labelledby="start-h" className="b-start">
      <h2 id="start-h">How do you want to start?</h2>
      <div className="b-choices">
        <button type="button" className="b-choice" onClick={() => pick({ form: blankForm(), fixtures: {}, info: {} })}>
          <strong>Blank scenario</strong>
          <span>An empty form with safe defaults. You add the organisation, personas, mailboxes and messages.</span>
        </button>
        <button type="button" className="b-choice" onClick={() => pick(templateDraft())}>
          <strong>Finance example (two personas)</strong>
          <span>
            A finance manager (the agent’s mailbox) receives an invoice from a vendor, who follows up after ten minutes.
            Change anything you like.
          </span>
        </button>
      </div>
    </section>
  );
}

function Builder({ mode, scenarioKey, initial, storageKey, restored, metaQ }: {
  mode: BuilderMode;
  scenarioKey: string;
  initial: Loaded;
  storageKey: string;
  restored: boolean;
  metaQ: { data?: BuilderMeta; isError: boolean };
}) {
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(initial.draft);
  const baseline = initial.baseline;
  const [section, setSection] = useState<SectionId>('basics');
  const [focusPath, setFocusPath] = useState<string | null>(null);
  const [showYaml, setShowYaml] = useState(false);
  const [notice, setNotice] = useState<string | null>(restored ? 'Restored your unsaved draft from this browser tab.' : null);
  const [fixturesKept, setFixturesKept] = useState(true);
  const [publishError, setPublishError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const allowLeave = useRef(false);
  const meta = metaQ.data ?? FALLBACK_META;
  const idLocked = mode === 'edit';

  // ---- edits
  const updateForm = useCallback((fn: (f: ScenarioForm) => void) => {
    setDraft((d) => {
      const form = structuredClone(d.form);
      fn(form);
      return { ...d, form };
    });
  }, []);
  const addFixture = useCallback((path: string, b64: string, info: FixtureInfo, owner: string) => {
    setDraft((d) => {
      const form = structuredClone(d.form);
      form.mailboxes.find((m) => m.owner === owner)?.messages.push(path);
      return { form, fixtures: { ...d.fixtures, [path]: b64 }, info: { ...d.info, [path]: info } };
    });
  }, []);
  const removeMessage = useCallback((owner: string, path: string) => {
    setDraft((d) => {
      const form = structuredClone(d.form);
      const mailbox = form.mailboxes.find((m) => m.owner === owner);
      if (mailbox) mailbox.messages = mailbox.messages.filter((p) => p !== path);
      if (referencedFixtures(form).includes(path)) return { ...d, form };
      const { [path]: _gone, ...fixtures } = d.fixtures;
      const { [path]: _goneInfo, ...info } = d.info;
      return { form, fixtures, info };
    });
  }, []);
  const setInfo = useCallback((path: string, info: FixtureInfo) => {
    setDraft((d) => ({ ...d, info: { ...d.info, [path]: info } }));
  }, []);

  // ---- loaded fixtures: read their headers once (for summaries and reply threading)
  const inspecting = useRef(new Set<string>());
  useEffect(() => {
    for (const [path, b64] of Object.entries(draft.fixtures)) {
      if (draft.info[path] || inspecting.current.has(path)) continue;
      inspecting.current.add(path);
      api.builder
        .inspectEml(b64)
        .then((s) => setInfo(path, { ...s, source: 'loaded' }))
        .catch(() =>
          setInfo(path, {
            source: 'loaded',
            message_id: null,
            in_reply_to: null,
            from: null,
            to: [],
            subject: `${path} (could not be read)`,
            attachments: [],
            text_preview: '',
          }),
        );
    }
  }, [api, draft.fixtures, draft.info, setInfo]);

  // ---- live validation
  const docKey = documentKey(draft.form);
  const fixtures = useMemo(() => usedFixtures(draft), [draft]);
  const liveKey = `${docKey}\n${fixturesSignature(fixtures)}`;
  const settledKey = useDebounced(liveKey, LIVE_DEBOUNCE_MS);
  const fixturesRef = useRef(fixtures);
  fixturesRef.current = fixtures;
  const validation = useQuery({
    queryKey: ['scenario-draft-validate', settledKey],
    queryFn: ({ signal }) =>
      api.builder.validate({ document: JSON.parse(settledKey.split('\n')[0] as string), fixtures: fixturesRef.current }, signal),
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    retry: false,
  });
  const stale = settledKey !== liveKey || validation.isFetching || validation.isPlaceholderData;
  const issues: IndexedIssue[] = useMemo(() => indexIssues(validation.data?.errors ?? []), [validation.data]);
  const state: ValidationState = stale
    ? 'validating'
    : validation.isError
      ? 'error'
      : validation.data?.ok
        ? 'valid'
        : 'invalid';
  const dirty = docKey !== baseline;

  // ---- unsaved draft in sessionStorage (flushed on unmount unless saved/discarded)
  const latest = useRef({ draft, dirty });
  latest.current = { draft, dirty };
  const discarded = useRef(false);
  useEffect(() => {
    const t = setTimeout(() => {
      if (dirty) setFixturesKept(saveDraft(storageKey, draft, baseline).fixturesKept);
      else clearDraft(storageKey);
    }, PERSIST_MS);
    return () => clearTimeout(t);
  }, [draft, dirty, storageKey, baseline]);
  useEffect(
    () => () => {
      if (!discarded.current && latest.current.dirty) saveDraft(storageKey, latest.current.draft, baseline);
    },
    [storageKey, baseline],
  );
  const discard = () => {
    discarded.current = true;
    clearDraft(storageKey);
  };

  // ---- focus a field after switching to its section
  useEffect(() => {
    if (!focusPath) return;
    const target = findTarget(focusPath) ?? document.getElementById(`sec-${section}-h`);
    target?.focus();
    setFocusPath(null);
  }, [focusPath, section]);
  const goTo = (issue: IndexedIssue) => {
    setSection(issue.section);
    setFocusPath(issue.key);
  };
  const openSection = (id: SectionId) => {
    setSection(id);
    setFocusPath(`sec:${id}`);
  };

  const onApplyYaml = (imported: Imported) => {
    const form = imported.form;
    const notes: string[] = ['YAML applied to the form.'];
    if (idLocked && form.id !== draft.form.id) {
      form.id = draft.form.id;
      notes.push('The scenario id cannot change while editing (use Clone); it was kept.');
    }
    if (imported.droppedAssertions > 0) {
      notes.push(
        `${imported.droppedAssertions} inline evaluation assertion(s) were removed: grading belongs in the answer key, written separately.`,
      );
    }
    setDraft((d) => ({ ...d, form }));
    setNotice(notes.join(' '));
  };

  const save = async () => {
    setSaving(true);
    setPublishError(null);
    try {
      const out = await api.builder.publish({ document: toDocument(draft.form), fixtures });
      discard();
      allowLeave.current = true;
      await queryClient.invalidateQueries({ queryKey: ['scenarios'] });
      await queryClient.invalidateQueries({ queryKey: ['scenario', out.scenario_key] });
      navigate(`/scenarios/${encodeURIComponent(out.scenario_key)}`, {
        state: { published: { version: out.version, mode } },
      });
    } catch (err) {
      setPublishError(err);
      setSaving(false);
    }
  };

  const value: BuilderValue = { api, draft, meta, idLocked, issues, updateForm, addFixture, removeMessage, setInfo };
  const counts = (id: SectionId) => issues.filter((i) => i.section === id).length;
  const index = SECTIONS.findIndex((s) => s.id === section);
  const canSave = state === 'valid' && !saving;
  const heading = mode === 'new' ? 'New scenario' : mode === 'edit' ? `Edit ${scenarioKey} (new version)` : `Clone of ${scenarioKey}`;

  return (
    <BuilderContext.Provider value={value}>
      <h1>{heading}</h1>
      {notice && (
        <div className="notice b-notice" role="status" data-testid="builder-notice">
          <span>{notice}</span>{' '}
          <button type="button" className="link" onClick={() => setNotice(null)}>
            Dismiss
          </button>
        </div>
      )}
      {metaQ.isError && (
        <p className="notice" role="note">
          Could not load the server’s option lists; built-in defaults are shown. Validation still uses the server.
        </p>
      )}
      {!fixturesKept && (
        <p className="notice" role="note" data-testid="fixtures-not-kept">
          The message files are too large to keep in this tab’s draft. The form is kept, but if you reload you will need
          to add the messages again.
        </p>
      )}
      <div className="b-toolbar">
        <StatusBadge state={state} count={issues.length} />
        <button type="button" aria-pressed={showYaml} aria-controls={showYaml ? "b-yaml-region" : undefined} onClick={() => setShowYaml(!showYaml)}>
          {showYaml ? 'Hide YAML' : 'Show YAML'}
        </button>
        <button type="button" className="b-primary" disabled={!canSave} aria-describedby="save-hint" onClick={() => void save()}>
          {saving ? 'Publishing…' : mode === 'edit' ? `Publish version ${draft.form.version}` : 'Save and publish'}
        </button>
        <span id="save-hint" className="hint">
          {state === 'valid'
            ? 'Publishing creates an immutable version you can start runs from.'
            : 'Saving is available once the scenario is valid.'}
        </span>
      </div>
      {validation.isError && !stale && <ErrorDisplay error={validation.error} title="Could not validate the scenario" />}
      {publishError !== null && <ErrorDisplay error={publishError} title="Could not publish" />}
      <ErrorSummary issues={issues} onGoTo={goTo} />
      <div className={`b-layout${showYaml ? ' b-with-yaml' : ''}`}>
        <nav aria-label="Scenario sections" className="b-nav">
          <ol>
            {SECTIONS.map((s, i) => (
              <li key={s.id}>
                <button
                  type="button"
                  className="b-nav-item"
                  aria-current={section === s.id ? 'step' : undefined}
                  aria-label={`${i + 1}. ${s.label}${counts(s.id) > 0 ? `, ${counts(s.id)} ${counts(s.id) === 1 ? 'problem' : 'problems'}` : ''}`}
                  onClick={() => openSection(s.id)}
                >
                  <span>
                    {i + 1}. {s.label}
                  </span>
                  {counts(s.id) > 0 && (
                    <span className="badge badge-fail" aria-hidden="true">
                      {counts(s.id)}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ol>
        </nav>
        <div className="b-sections">
          {SECTIONS.map((s) => (
            <section key={s.id} aria-labelledby={`sec-${s.id}-h`} hidden={section !== s.id} className="b-section">
              <h2 id={`sec-${s.id}-h`} tabIndex={-1}>
                {s.label}
              </h2>
              {s.id === 'basics' && <BasicsSection />}
              {s.id === 'organisation' && <OrganisationSection />}
              {s.id === 'personas' && <PersonasSection />}
              {s.id === 'mailboxes' && <MailboxesSection />}
              {s.id === 'messages' && <MessagesSection />}
              {s.id === 'events' && <EventsSection />}
              {s.id === 'agent' && <AgentSection onGoTo={openSection} />}
              {s.id === 'limits' && <LimitsSection />}
            </section>
          ))}
          <div className="row space-between b-pager">
            <button type="button" disabled={index === 0} onClick={() => openSection((SECTIONS[index - 1] as (typeof SECTIONS)[number]).id)}>
              Previous section
            </button>
            <button
              type="button"
              disabled={index === SECTIONS.length - 1}
              onClick={() => openSection((SECTIONS[index + 1] as (typeof SECTIONS)[number]).id)}
            >
              Next section
            </button>
          </div>
        </div>
        {showYaml && (
          <div id="b-yaml-region">
            <YamlPanel onApply={onApplyYaml} />
          </div>
        )}
      </div>
      <LeaveGuard dirty={dirty && !saving} allowRef={allowLeave} onDiscard={discard} />
    </BuilderContext.Provider>
  );
}

function BuilderLoader({ mode, scenarioKey }: { mode: BuilderMode; scenarioKey: string }) {
  const api = useApi();
  const [params] = useSearchParams();
  const storageKey = draftStorageKey(mode, scenarioKey);
  const [stored] = useState(() => loadDraft(storageKey));
  const [picked, setPicked] = useState<Loaded | null>(null);
  const metaQ = useQuery({ queryKey: ['authoring-meta'], queryFn: () => api.builder.meta(), staleTime: Infinity, retry: false });
  const needsLoad = mode !== 'new' && !stored;
  const scenarioQ = useQuery({ queryKey: ['scenario', scenarioKey], queryFn: () => api.getScenario(scenarioKey), enabled: needsLoad });
  const from = Number(params.get('from'));
  const version = from > 0 ? from : scenarioQ.data?.latest_version?.version;
  const docQ = useQuery({
    queryKey: ['scenario-document', scenarioKey, version],
    queryFn: () => api.builder.versionDocument(scenarioKey, version as number),
    enabled: needsLoad && version !== undefined,
    staleTime: Infinity,
  });

  const loaded: Loaded | null = useMemo(() => {
    if (stored) return { draft: stored.draft, baseline: stored.baseline };
    if (picked) return picked;
    if (!docQ.data || !scenarioQ.data) return null;
    const { form } = fromDocument(docQ.data.document);
    if (mode === 'edit') {
      form.id = scenarioKey;
      form.version = String(Math.max(0, ...scenarioQ.data.versions) + 1);
    } else {
      form.id = `${form.id}-copy`;
      form.version = '1';
      form.title = form.title ? `${form.title} (copy)` : '';
    }
    return { draft: { form, fixtures: { ...docQ.data.fixtures }, info: {} }, baseline: documentKey(form) };
  }, [stored, picked, docQ.data, scenarioQ.data, mode, scenarioKey]);

  const intro =
    mode === 'new'
      ? 'Build a scenario step by step: who is involved, what is already in their mailboxes, what happens during a run, and what the agent may do. The form checks your work as you type; save when it is valid.'
      : mode === 'edit'
        ? `Change ${scenarioKey} and publish it as a new version. Earlier versions and their runs stay as they are.`
        : `Start a new scenario from a copy of ${scenarioKey}. Give it its own id before saving.`;
  const next = mode === 'new' ? [{ to: '/scenarios', label: 'All scenarios' }] : [{ to: `/scenarios/${encodeURIComponent(scenarioKey)}`, label: `Back to ${scenarioKey}` }];
  const error = scenarioQ.error ?? docQ.error;

  return (
    <>
      <PageIntro next={next}>
        <p>{intro}</p>
        <p className="hint">After publishing: write the scenario’s answer key (how runs are graded), then start a run.</p>
      </PageIntro>
      {loaded ? (
        <Builder
          mode={mode}
          scenarioKey={scenarioKey}
          initial={loaded}
          storageKey={storageKey}
          restored={!!stored}
          metaQ={metaQ}
        />
      ) : mode === 'new' ? (
        <>
          <h1>New scenario</h1>
          <Chooser onPick={setPicked} />
        </>
      ) : error ? (
        <ErrorDisplay error={error} title="Could not load the scenario" />
      ) : (
        <Loading label="Loading the scenario…" />
      )}
    </>
  );
}

export function ScenarioBuilderPage({ mode }: { mode: BuilderMode }) {
  const { key = '' } = useParams();
  const scenarioKey = mode === 'new' ? '' : key;
  usePageTitle(mode === 'new' ? 'New scenario' : mode === 'edit' ? `Edit ${scenarioKey}` : `Clone ${scenarioKey}`);
  return (
    <RequireRole roles={['controller']} what="Building scenarios">
      <BuilderLoader key={`${mode}:${scenarioKey}`} mode={mode} scenarioKey={scenarioKey} />
    </RequireRole>
  );
}
