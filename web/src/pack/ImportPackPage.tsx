// /scenarios/import: one step from a scenario pack (folder or .zip with scenario.yaml, its .eml
// fixtures and an optional answer_key.yaml) to a published scenario and answer key.
// The pack is read in the browser; the API validates and publishes it through the same routes
// as the builder and the answer-key editor (scenario: controller; answer key: controller or
// evaluator), so every server-side rule applies, duplicate Message-IDs included.
import { useQueryClient } from '@tanstack/react-query';
import { useId, useState, type ChangeEvent } from 'react';
import { Link } from 'react-router-dom';
import { isApiError } from '../api/client';
import type { AnswerKeyOut, ScenarioVersionOut, ValidationIssue } from '../api/types';
import { useApi } from '../auth/session';
import { fileBytes, formatBytes } from '../builder/files';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { RequireRole } from '../components/Gates';
import { PageIntro } from '../components/PageIntro';
import { usePageTitle } from '../hooks/usePageTitle';
import { buildPack, PACK_LIMITS, readFolderPack, readZipPack, type Pack } from './pack';

interface Checked {
  pack: Pack;
  scenarioId: string;
  version: string;
  title: string;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  keyErrors: ValidationIssue[];
  /** The answer key's references can only be checked once the scenario exists. */
  keyDeferred: boolean;
}

interface Published {
  scenario: ScenarioVersionOut;
  answerKey: AnswerKeyOut | null;
  keyErrors: ValidationIssue[];
  keyError: unknown;
}

type Step =
  | { kind: 'idle' }
  | { kind: 'busy'; label: string }
  | { kind: 'failed'; error: unknown; checked?: Checked }
  | { kind: 'checked'; checked: Checked }
  | { kind: 'published'; checked: Checked; published: Published };

const str = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');

function Issues({ title, issues, testId }: { title: string; issues: ValidationIssue[]; testId: string }) {
  if (issues.length === 0) return null;
  return (
    <div data-testid={testId}>
      <h3>{title}</h3>
      <ul>
        {issues.map((i, n) => (
          <li key={`${n}:${i.path}`}>
            <code>{i.path}</code>: {i.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Importer() {
  const api = useApi();
  const queryClient = useQueryClient();
  const id = useId();
  const [step, setStep] = useState<Step>({ kind: 'idle' });

  const check = async (pack: Pack): Promise<Checked> => {
    const result = await api.builder.validate({ source: pack.source, fixtures: pack.fixtures });
    const scenario = (result.document?.scenario ?? {}) as Record<string, unknown>;
    const referenced = new Set(
      (Array.isArray(scenario.mailboxes) ? scenario.mailboxes : []).flatMap((m: unknown) => {
        const list = (m as { initial_messages?: unknown }).initial_messages;
        return Array.isArray(list) ? list.map((im: unknown) => str((im as { fixture?: unknown }).fixture)) : [];
      }),
    );
    const unused = Object.keys(pack.fixtures).filter((p) => !referenced.has(p));
    const warnings = [...result.warnings, ...unused.map((p) => ({ path: p, message: 'not listed by any mailbox: not published' }))];
    const scenarioId = str(scenario.id);
    let keyErrors: ValidationIssue[] = [];
    let keyDeferred = false;
    if (pack.answerKeySource !== null && scenarioId) {
      try {
        const key = await api.validateAnswerKey(scenarioId, { source: pack.answerKeySource });
        keyErrors = key.errors;
      } catch (err) {
        if (!isApiError(err) || err.status !== 404) throw err;
        keyDeferred = true; // a new scenario: the key's references are checked after publishing it
      }
    }
    return { pack, scenarioId, version: str(scenario.version), title: str(scenario.title), errors: result.errors, warnings, keyErrors, keyDeferred };
  };

  const load = async (read: () => Promise<Pack>) => {
    setStep({ kind: 'busy', label: 'Reading and checking the pack…' });
    try {
      const checked = await check(await read());
      setStep({ kind: 'checked', checked });
    } catch (error) {
      setStep({ kind: 'failed', error });
    }
  };

  const onFolder = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files as FileList);
    e.target.value = '';
    if (files.length > 0) void load(async () => buildPack(await readFolderPack(files)));
  };

  const onZip = (e: ChangeEvent<HTMLInputElement>) => {
    const file = (e.target.files as FileList)[0];
    e.target.value = '';
    if (!file) return;
    void load(async () => {
      if (file.size > PACK_LIMITS.maxZipBytes) throw new Error(`${file.name} is larger than ${formatBytes(PACK_LIMITS.maxZipBytes)}.`);
      return buildPack(await readZipPack(await fileBytes(file)));
    });
  };

  const publish = async (checked: Checked) => {
    const { pack } = checked;
    setStep({ kind: 'busy', label: 'Publishing…' });
    let scenario: ScenarioVersionOut;
    try {
      scenario = await api.builder.publish({ source: pack.source, fixtures: pack.fixtures });
    } catch (error) {
      setStep({ kind: 'failed', error, checked });
      return;
    }
    const published: Published = { scenario, answerKey: null, keyErrors: [], keyError: null };
    if (pack.answerKeySource !== null) {
      try {
        const body = { source: pack.answerKeySource };
        const check = await api.validateAnswerKey(scenario.scenario_key, body);
        published.keyErrors = check.errors;
        if (check.ok) published.answerKey = await api.createAnswerKey(scenario.scenario_key, body);
      } catch (error) {
        published.keyError = error;
      }
    }
    await queryClient.invalidateQueries({ queryKey: ['scenarios'] });
    await queryClient.invalidateQueries({ queryKey: ['scenario', scenario.scenario_key] });
    setStep({ kind: 'published', checked, published });
  };

  const busy = step.kind === 'busy';
  const checked = step.kind === 'checked' || step.kind === 'published' ? step.checked : step.kind === 'failed' ? step.checked : undefined;
  const blocked = !!checked && (checked.errors.length > 0 || checked.keyErrors.length > 0);

  return (
    <>
      <section aria-labelledby={`${id}-pick`} className="card">
        <h2 id={`${id}-pick`}>1. Choose the pack</h2>
        <p className="hint">
          A folder or a .zip with <code>scenario.yaml</code> (or <code>.json</code>), the <code>.eml</code> files it lists
          (paths relative to it, e.g. <code>fixtures/001-hello.eml</code>) and, optionally, <code>answer_key.yaml</code>. Other
          files are not read. Limits: {PACK_LIMITS.maxFixtures} message files of at most {formatBytes(PACK_LIMITS.maxEntryBytes)}, {formatBytes(PACK_LIMITS.maxTotalBytes)}{' '}
          in total.
        </p>
        <div className="row">
          <label htmlFor={`${id}-folder`}>Choose a folder</label>
          <input id={`${id}-folder`} type="file" multiple disabled={busy} onChange={onFolder} {...{ webkitdirectory: '', directory: '' }} />
          <label htmlFor={`${id}-zip`}>Choose a .zip file</label>
          <input id={`${id}-zip`} type="file" accept=".zip,application/zip" disabled={busy} onChange={onZip} />
        </div>
        {busy && <p role="status">{step.label}</p>}
      </section>
      {step.kind === 'failed' && <ErrorDisplay error={step.error} title={step.checked ? 'Could not publish' : 'Could not read the pack'} />}
      {checked && (
        <section aria-labelledby={`${id}-check`} className="card" data-testid="pack-check">
          <h2 id={`${id}-check`}>2. Check</h2>
          <dl className="kv">
            <dt>Scenario</dt>
            <dd>
              <code>{checked.scenarioId || '(no id)'}</code> version {checked.version || '?'}
              {checked.title && `: ${checked.title}`}
            </dd>
            <dt>Read from</dt>
            <dd>
              <code>{checked.pack.scenarioFile}</code>
            </dd>
            <dt>Message files</dt>
            <dd>{Object.keys(checked.pack.fixtures).length}</dd>
            <dt>Answer key</dt>
            <dd>
              {checked.pack.answerKeyFile ? (
                <>
                  <code>{checked.pack.answerKeyFile}</code>
                  {checked.keyDeferred && ' (its references are checked when the scenario is published)'}
                </>
              ) : (
                'none in the pack'
              )}
            </dd>
          </dl>
          <Issues title={`${checked.errors.length} problem(s) in the scenario`} issues={checked.errors} testId="pack-errors" />
          <Issues title={`${checked.keyErrors.length} problem(s) in the answer key`} issues={checked.keyErrors} testId="pack-key-errors" />
          <Issues title="Warnings" issues={checked.warnings} testId="pack-warnings" />
          {checked.pack.ignored.length > 0 && (
            <details data-testid="pack-ignored">
              <summary>{checked.pack.ignored.length} file(s) not read</summary>
              <ul>
                {checked.pack.ignored.map((p) => (
                  <li key={p}>
                    <code>{p}</code>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {step.kind !== 'published' && (
            <p className="row">
              <button type="button" className="b-primary" disabled={busy || blocked} onClick={() => void publish(checked)}>
                {checked.pack.answerKeySource !== null ? 'Publish scenario and answer key' : 'Publish scenario'}
              </button>
              {blocked && <span className="hint">Fix the problems in the pack and choose it again.</span>}
            </p>
          )}
        </section>
      )}
      {step.kind === 'published' && <Result published={step.published} />}
    </>
  );
}

function Result({ published }: { published: Published }) {
  const { scenario, answerKey, keyErrors, keyError } = published;
  const key = encodeURIComponent(scenario.scenario_key);
  return (
    <section role="status" aria-labelledby="pack-done-h" className="card" data-testid="pack-published">
      <h2 id="pack-done-h">3. Published</h2>
      <p>
        <code>{scenario.scenario_key}</code> version {scenario.version} is published.
        {answerKey && ` Answer key version ${answerKey.version} is published (hidden from agents).`}
      </p>
      <Issues title="The answer key was not published" issues={keyErrors} testId="pack-key-errors" />
      {keyError !== null && <ErrorDisplay error={keyError} title="The answer key was not published" />}
      <ul>
        <li>
          <Link to={`/scenarios/${key}`}>Open the scenario</Link>
        </li>
        <li>
          <Link to={`/scenarios/${key}/start`}>Start a run</Link>
        </li>
        {!answerKey && (
          <li>
            <Link to={`/scenarios/${key}/answer-key`}>Write its answer key</Link>
          </li>
        )}
      </ul>
    </section>
  );
}

export function ImportPackPage() {
  usePageTitle('Import a scenario pack');
  return (
    <>
      <h1>Import a scenario pack</h1>
      <PageIntro next={[{ to: '/scenarios', label: 'All scenarios' }]}>
        <p>
          Publish a scenario that was written as files (for example one of the bundled packs under <code>scenarios/</code>)
          in one step: the pack is checked first, then the scenario is published and, when the pack has one, its answer
          key.
        </p>
      </PageIntro>
      <RequireRole roles={['controller']} what="Importing scenario packs">
        <Importer />
      </RequireRole>
    </>
  );
}
