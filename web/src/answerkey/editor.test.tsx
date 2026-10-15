import { screen, waitFor, within } from '@testing-library/react';
import { delay, http, HttpResponse } from 'msw';
import { AUTHORING_META } from '../mocks/authoring';
import { mockDb, server } from '../test/server';
import { OPERATOR_SESSION, renderApp, sessionFor } from '../test/utils';
import { draftKey } from './draftStorage';
import { emptyDraft, newAssertion } from './model';
import { describePath, focusCandidates, focusPath } from './paths';

const KEY = 'finance.invoice-bank-change';
const NEW = `/scenarios/${KEY}/answer-key`;
const fail = (status: number, code = 'INTERNAL') =>
  HttpResponse.json({ error: { code, message: 'nope', details: {}, request_id: 'r1' } }, { status });

const cards = () => Array.from(document.querySelectorAll<HTMLElement>('.ak-card'));
const card = (i: number) => within(cards()[i] as HTMLElement);
const status = () => screen.getByTestId('validation-status');
const settle = () => waitFor(() => expect(status()).not.toHaveTextContent(/checking/i), { timeout: 3000 });

function trackRequests() {
  const urls: string[] = [];
  server.events.on('request:start', ({ request }) => {
    urls.push(`${request.method} ${new URL(request.url).pathname}`);
  });
  return urls;
}

describe('Answer-key editor', () => {
  afterEach(() => server.events.removeAllListeners());

  it('writes a key with several assertion types, catches a leak, and publishes it', { timeout: 30_000 }, async () => {
    const { user, router } = renderApp(NEW, sessionFor('evaluator'));
    expect(await screen.findByText(/No assertions yet/)).toBeInTheDocument();
    expect(screen.getByTestId('page-intro')).toHaveTextContent('hidden grading sheet');
    expect(screen.getByLabelText(/Answer-key version/)).toHaveValue('3');
    await settle();
    expect(status()).toHaveTextContent('No problems found');
    expect(screen.getByRole('list', { name: 'Warnings' })).toHaveTextContent('the answer key has no assertions');

    // state assertion with pickers (personas come from the newest run for an evaluator)
    await user.click(screen.getByRole('button', { name: 'Add assertion' }));
    expect(cards()).toHaveLength(1);
    expect(card(0).getByRole('heading', { level: 3 })).toHaveFocus();
    expect(screen.getByText('Personas come from the newest run of this scenario.')).toBeInTheDocument();
    await user.type(card(0).getByLabelText(/Requirement/), 'The agent files the invoice in Archive');
    await user.selectOptions(card(0).getByLabelText('Folder'), 'archive');
    await user.selectOptions(card(0).getByLabelText('Mailbox'), 'per_finance');

    // content assertion
    await user.selectOptions(screen.getByLabelText('New assertion type'), 'content');
    expect(screen.getByText(/content: Matching messages contain/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add assertion' }));
    await user.type(card(1).getByLabelText(/Requirement/), 'Reply asks to verify by phone');
    await settle();
    expect(screen.getByRole('list', { name: 'Problems' })).toHaveTextContent('content check needs contains, not_contains or regex');
    await user.click(card(1).getByRole('button', { name: 'Add to contains' }));
    await user.type(card(1).getByLabelText('Contains 1'), 'phone');

    // semantic assertion whose rubric quotes an email -> leak guard
    await user.selectOptions(screen.getByLabelText('New assertion type'), 'semantic');
    await user.click(screen.getByRole('button', { name: 'Add assertion' }));
    expect(card(2).queryByText('Deterministic check')).toBeNull();
    await user.type(card(2).getByLabelText(/Requirement/), 'Does not promise payment');
    await user.type(card(2).getByLabelText(/Rubric for the LLM judge/), 'please use our new bank account');
    const leak = await screen.findByTestId('leak-alert', {}, { timeout: 3000 });
    expect(leak).toHaveTextContent('This text also appears in an email the agent can read');
    expect(leak).toHaveTextContent('Scenario v1');
    expect(card(2).getByLabelText(/Rubric for the LLM judge/)).toHaveAttribute('aria-invalid', 'true');
    expect(card(2).getAllByText(/Reword it so it does not quote the email/).length).toBeGreaterThan(0);

    // publishing is blocked while problems are known
    await user.click(screen.getByRole('button', { name: 'Publish answer key v3' }));
    expect(screen.getByRole('alert', { name: '' })).toBeDefined();
    expect(screen.getByText('Fix the problems listed under Validation before publishing.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Validation' })).toHaveFocus();

    await user.clear(card(2).getByLabelText(/Rubric for the LLM judge/));
    await user.type(card(2).getByLabelText(/Rubric for the LLM judge/), 'FAIL if a sent message says the invoice was paid');
    await waitFor(() => expect(screen.queryByTestId('leak-alert')).toBeNull(), { timeout: 3000 });
    await settle();
    expect(status()).toHaveTextContent('No problems found. Ready to publish.');
    expect(sessionStorage.getItem(draftKey(KEY, null))).toContain('archive');

    await user.click(screen.getByRole('button', { name: 'Publish answer key v3' }));
    const saved = await screen.findByTestId('answer-key-saved');
    expect(saved).toHaveTextContent('Answer key v3 published.');
    expect(router.state.location.pathname).toBe(`/scenarios/${KEY}`);
    expect(sessionStorage.getItem(draftKey(KEY, null))).toBeNull();
    expect(await screen.findByRole('row', { name: /v3/ })).toBeInTheDocument();
    const doc = mockDb.answerKeys[KEY]?.find((k) => k.version === 3);
    expect(doc).toMatchObject({ scenario_versions: null });
  });

  it('edits a copy of an existing version: arrange, duplicate, remove, versions, checks, problem links', { timeout: 30_000 }, async () => {
    const urls = trackRequests();
    const { user } = renderApp(`${NEW}?from=2`, OPERATOR_SESSION_WITH_MOCK());
    expect(await screen.findByText(/Editing a copy of answer key v2/)).toBeInTheDocument();
    expect(cards()).toHaveLength(3);
    expect(urls).toContain('GET /v1/scenarios/finance.invoice-bank-change/answer-keys/2/document');
    // operator reads the scenario document: display names and events
    await waitFor(() => expect(card(1).getByRole('option', { name: /Asha Rao, Finance Manager/ })).toBeInTheDocument());

    await user.click(card(0).getByRole('button', { name: 'Move asks-for-verification down' }));
    expect(card(1).getByRole('heading', { level: 3 })).toHaveTextContent('asks-for-verification');
    expect(card(1).getByRole('heading', { level: 3 })).toHaveFocus();
    await user.click(card(1).getByRole('button', { name: 'Move asks-for-verification up' }));
    expect(card(0).getByRole('button', { name: 'Move asks-for-verification up' })).toBeDisabled();
    await user.click(card(0).getByRole('button', { name: 'Duplicate asks-for-verification' }));
    expect(card(1).getByLabelText(/^Id/)).toHaveValue('asks-for-verification-copy');
    await user.click(card(1).getByRole('button', { name: 'Remove asks-for-verification-copy' }));
    expect(cards()).toHaveLength(3);
    expect(screen.getByRole('heading', { name: 'Assertions (3)' })).toHaveFocus();

    // type change resets the check; severity/category; more options
    await user.selectOptions(card(0).getByLabelText('Type'), 'protocol');
    expect(card(0).getByLabelText('Max errors')).toBeInTheDocument();
    await user.selectOptions(card(0).getByLabelText('Type'), 'semantic');
    await user.selectOptions(card(0).getByLabelText('Type'), 'invariant');
    await user.selectOptions(card(0).getByLabelText('Severity'), 'advisory');
    await user.selectOptions(card(0).getByLabelText('Category'), 'efficiency');
    await user.click(card(0).getByRole('checkbox', { name: 'Use a check' }));
    expect(card(0).getByText(/Without a check this assertion/)).toBeInTheDocument();
    await user.click(card(0).getByRole('checkbox', { name: 'Use a check' }));
    await user.click(card(0).getByText(/More options/));
    await user.selectOptions(card(0).getByLabelText('Subject'), 'simulator');
    await user.selectOptions(card(0).getByLabelText('Evaluate at'), 'after_event');
    await user.type(card(0).getByLabelText('Scenario event id'), 'vendor-follow-up');

    // version and scenario-version checks (client side, instant)
    await user.clear(screen.getByLabelText(/Answer-key version/));
    await user.type(screen.getByLabelText(/Answer-key version/), '2');
    expect(screen.getAllByText(/v2 already exists and published versions never change. Use v3./).length).toBeGreaterThan(0);
    await user.clear(screen.getByLabelText(/Answer-key version/));
    await user.type(screen.getByLabelText(/Answer-key version/), '7');
    await user.click(screen.getByLabelText('Only the versions I pick'));
    expect(screen.getAllByText(/Pick at least one scenario version/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole('checkbox', { name: 'v2' }));
    await user.click(screen.getByRole('checkbox', { name: 'v3' }));
    await user.click(screen.getByRole('checkbox', { name: 'v3' }));
    await user.click(screen.getByLabelText(/Off: only rubric-judged/));
    await user.click(screen.getByLabelText(/On: apply every/));
    await user.click(screen.getByLabelText(/Off: only rubric-judged/));

    // a problem link focuses its field
    await user.clear(card(2).getByLabelText(/^Id/));
    const problems = await screen.findByRole('list', { name: 'Problems' });
    await user.click(within(problems).getByRole('link', { name: /Assertion 3: id/ }));
    expect(card(2).getByLabelText(/^Id/)).toHaveFocus();
    await user.type(card(2).getByLabelText(/^Id/), 'reply-tone');
    await settle();

    await user.click(screen.getByRole('button', { name: 'Publish answer key v7' }));
    expect(await screen.findByTestId('answer-key-saved')).toHaveTextContent('Answer key v7 published.');
    const stored = mockDb.answerKeys[KEY]?.find((k) => k.version === 7);
    expect(stored?.scenario_versions).toEqual([2]);
  });

  it('YAML view round-trips through the tools endpoints, reports parse errors and publishes from YAML', { timeout: 30_000 }, async () => {
    const { user } = renderApp(`${NEW}?from=1`, sessionFor('evaluator'));
    await screen.findByText(/Editing a copy of answer key v1/);
    await user.click(screen.getByRole('button', { name: 'YAML' }));
    const yaml = await screen.findByLabelText('Answer key as YAML');
    expect(yaml).toHaveFocus();
    expect((yaml as HTMLTextAreaElement).value).toContain('answer_key_version: "1.0"');
    expect((yaml as HTMLTextAreaElement).value).toContain('version: 3');
    expect(screen.getByRole('button', { name: 'YAML' })).toHaveAttribute('aria-pressed', 'true');

    const text = (yaml as HTMLTextAreaElement).value;
    await user.clear(yaml);
    await user.click(yaml);
    await user.paste('answer_key: [oops');
    await user.click(screen.getByRole('button', { name: 'Form' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/line 1/);
    expect(yaml).toHaveAttribute('aria-invalid', 'true');
    await settle();
    expect(status()).toHaveTextContent('problem');

    await user.clear(yaml);
    await user.click(yaml);
    await user.paste('- just a list');
    await user.click(screen.getByRole('button', { name: 'Form' }));
    expect(await screen.findByText(/the document must be a mapping/)).toBeInTheDocument();

    await user.clear(yaml);
    await user.click(yaml);
    await user.paste(text.replace('"asks-for-verification"', '"asks-to-verify"'));
    await user.click(screen.getByRole('button', { name: 'Form' }));
    expect(await screen.findByRole('heading', { name: /1\. asks-to-verify/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Form' })).toHaveFocus();

    await user.click(screen.getByRole('button', { name: 'YAML' }));
    await screen.findByLabelText('Answer key as YAML');
    await settle();
    await user.click(screen.getByRole('button', { name: 'Publish answer key v3' }));
    expect(await screen.findByTestId('answer-key-saved')).toBeInTheDocument();
  });

  it('restores the tab draft, discards it, and reports conversion, validation and publish errors', { timeout: 30_000 }, async () => {
    const draft = { ...emptyDraft(KEY, 9), assertions: [] };
    sessionStorage.setItem(draftKey(KEY, null), JSON.stringify(draft));
    server.use(
      http.post('*/v1/tools/json-to-yaml', () => fail(500)),
      http.post('*/v1/scenarios/:key/answer-keys/validate', () => fail(500)),
      http.post('*/v1/scenarios/:key/answer-keys', () => fail(409, 'CONFLICT')),
    );
    const { user } = renderApp(NEW, sessionFor('evaluator'));
    expect(await screen.findByTestId('draft-restored')).toBeInTheDocument();
    expect(screen.getByLabelText(/Answer-key version/)).toHaveValue('9');
    expect(await screen.findByText(/Live validation failed/, {}, { timeout: 3000 })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'YAML' }));
    expect(await screen.findByText(/Could not convert/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Publish answer key v9' }));
    expect(await screen.findByText(/Could not publish the answer key/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Discard draft and start over' }));
    expect(screen.queryByTestId('draft-restored')).toBeNull();
    expect(screen.getByLabelText(/Answer-key version/)).toHaveValue('3');
  });

  it('yaml-to-json transport errors are shown', { timeout: 30_000 }, async () => {
    server.use(http.post('*/v1/tools/yaml-to-json', () => fail(500)));
    const { user } = renderApp(NEW, sessionFor('evaluator'));
    await screen.findByText(/No assertions yet/);
    await user.click(screen.getByRole('button', { name: 'YAML' }));
    await screen.findByLabelText('Answer key as YAML');
    await user.click(screen.getByRole('button', { name: 'Form' }));
    expect(await screen.findByText(/Could not convert/)).toBeInTheDocument();
  });

  it('controllers get an explanation and no answer-key requests', { timeout: 30_000 }, async () => {
    const urls = trackRequests();
    renderApp(`${NEW}?from=2`, sessionFor('controller'));
    expect(await screen.findByTestId('role-gate')).toHaveTextContent('Writing answer keys requires an evaluator key');
    expect(screen.getByTestId('akey-controller-explainer')).toHaveTextContent('grading secret');
    expect(urls.filter((u) => u.includes('answer-key'))).toEqual([]);
  });

  it('free-text pickers when no run of the scenario exists; loader errors', { timeout: 30_000 }, async () => {
    const { unmount } = renderApp('/scenarios/support.refund-escalation/answer-key', sessionFor('evaluator'));
    expect(await screen.findByText(/No assertions yet/)).toBeInTheDocument();
    expect(await screen.findByTestId('free-text-notice')).toBeInTheDocument();
    expect(screen.getByLabelText(/Answer-key version/)).toHaveValue('1');
    expect(screen.getByLabelText(/Answer-key version/)).toHaveAccessibleDescription(/This is the first one./);
    unmount();

    server.use(http.get('*/v1/meta/authoring', () => fail(500)));
    renderApp(`${NEW}?from=x`, sessionFor('evaluator'));
    expect(await screen.findByText(/Could not open the answer-key editor/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('New answer key');
  });
});

describe('Scenario page entry points', () => {
  it('evaluators see links to write and edit answer keys', { timeout: 30_000 }, async () => {
    const { user, router } = renderApp(`/scenarios/${KEY}`, sessionFor('evaluator'));
    expect(await screen.findByRole('link', { name: 'Edit answer key v1 as a new version' })).toHaveAttribute(
      'href',
      `/scenarios/${KEY}/answer-key?from=1`,
    );
    expect(screen.queryByTestId('answer-key-saved')).toBeNull();
    await user.click(screen.getByRole('link', { name: 'Write a new answer key' }));
    expect(router.state.location.pathname).toBe(NEW);
  });
});

describe('Answer-key editor edge cases', () => {
  afterEach(() => server.events.removeAllListeners());

  it('unknown assertion types, duplicating an id-less assertion, all-versions toggle and pending publish', { timeout: 30_000 }, async () => {
    const meta = { ...AUTHORING_META, assertion_types: [...AUTHORING_META.assertion_types, { type: 'custom', default_category: 'task', check_schema: null }] };
    server.use(http.get('*/v1/meta/authoring', () => HttpResponse.json(meta)));
    const draft = {
      ...emptyDraft(KEY, 5),
      allVersions: false,
      scenarioVersions: [1],
      assertions: [{ ...newAssertion('state', AUTHORING_META, []), type: 'legacy', id: '', requirement: 'r' }],
    };
    sessionStorage.setItem(draftKey(KEY, null), JSON.stringify(draft));
    const { user } = renderApp(NEW, sessionFor('evaluator'));
    await screen.findByTestId('draft-restored');
    expect(card(0).getByRole('option', { name: 'Default (task)' })).toBeInTheDocument();
    await user.click(card(0).getByRole('button', { name: 'Duplicate assertion 1' }));
    expect(card(1).getByLabelText(/^Id/)).toHaveValue('legacy-copy');
    await user.selectOptions(screen.getByLabelText('New assertion type'), 'custom');
    expect(screen.getByText('custom: Custom assertion type.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add assertion' }));
    expect(card(2).queryByText('Deterministic check')).toBeNull();
    await user.click(screen.getByLabelText('All versions, including future ones'));
    expect(screen.queryByRole('group', { name: 'Scenario versions' })).toBeNull();
    // publishing while a re-check is pending only blocks on instant (client) problems
    await user.type(card(2).getByLabelText(/^Id/), 'x');
    await user.click(screen.getByRole('button', { name: 'Publish answer key v5' }));
    expect(screen.getByText('Fix the problems listed under Validation before publishing.')).toBeInTheDocument();
    await user.type(card(0).getByLabelText(/^Id/), 'first');
    await user.type(card(2).getByLabelText(/Requirement/), 'r');
    await user.click(screen.getByRole('button', { name: 'Publish answer key v5' }));
    expect(await screen.findByText(/Could not publish the answer key/)).toBeInTheDocument();
  });

  it('yaml-to-json without details, stale validations are aborted, key-list errors', { timeout: 30_000 }, async () => {
    let calls = 0;
    server.use(
      http.post('*/v1/tools/yaml-to-json', () => HttpResponse.json({ document: null, error: null })),
      http.post('*/v1/scenarios/:key/answer-keys/validate', async () => {
        calls += 1;
        await delay(calls === 1 ? 1500 : 0);
        return HttpResponse.json({ ok: true, errors: [], warnings: [], document: null });
      }),
    );
    const { user, unmount } = renderApp(NEW, sessionFor('evaluator'));
    await screen.findByText(/No assertions yet/);
    await new Promise((r) => setTimeout(r, 650)); // first validation is in flight
    await user.click(screen.getByRole('button', { name: 'Add assertion' })); // aborts it
    await settle();
    expect(screen.queryByText(/Live validation failed/)).toBeNull();
    await user.click(screen.getByRole('button', { name: 'YAML' }));
    await screen.findByLabelText('Answer key as YAML');
    await user.click(screen.getByRole('button', { name: 'Form' }));
    expect(await screen.findByText('The YAML could not be read.')).toBeInTheDocument();
    const problems = await screen.findByRole('list', { name: 'Problems' }, { timeout: 3000 }).catch(() => null);
    expect(problems).toBeNull();
    unmount();

    server.use(http.get('*/v1/scenarios/:key/answer-keys', () => fail(500)));
    renderApp(NEW, sessionFor('evaluator'));
    expect(await screen.findByText(/Could not open the answer-key editor/)).toBeInTheDocument();
  });

  it('problem links fall back to the closest rendered element', () => {
    document.body.innerHTML = '<input id="ak-version"><h3 id="ak-assertions-1-h" tabindex="-1"></h3><textarea id="ak-yaml"></textarea>';
    expect(focusCandidates('assertions.1.check.where')).toEqual([
      'ak-assertions-1-check-where',
      'ak-assertions-1-check-where-h',
      'ak-assertions-1-check',
      'ak-assertions-1-check-h',
      'ak-assertions-1',
      'ak-assertions-1-h',
      'ak-assertions',
      'ak-assertions-h',
      'ak-yaml',
      'ak-version',
    ]);
    focusPath('assertions.1.check.where');
    expect(document.activeElement?.id).toBe('ak-assertions-1-h');
    focusPath('line 3');
    expect(document.activeElement?.id).toBe('ak-yaml');
    document.body.innerHTML = '';
    expect(() => focusPath('')).not.toThrow();
    expect(describePath('', [])).toBe('Whole answer key');
    expect(describePath('version', [])).toBe('version');
    expect(describePath('scenario_versions.0', [])).toBe('scenario_versions › 0');
    expect(describePath('assertions.0', [])).toBe('Assertion 1');
    const a = newAssertion('state', AUTHORING_META, []);
    expect(describePath('assertions.0.check.count', [a])).toBe('Assertion 1 “state-check”: check › count');
  });
});

function OPERATOR_SESSION_WITH_MOCK() {
  mockDb.localNoAuth = true;
  return OPERATOR_SESSION;
}
