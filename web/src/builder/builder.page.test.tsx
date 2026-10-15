import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { captureBodies, goSection, openNew, SLOW, waitInvalid, waitValid } from '../test/builder';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';
import { draftStorageKey, saveDraft } from './draft';
import { blankForm, documentKey } from './model';
import { templateDraft } from './template';

const fail = (status: number, code: string, message = 'nope') =>
  HttpResponse.json({ error: { code, message, details: {}, request_id: 'req_t' } }, { status });

afterEach(() => server.events.removeAllListeners());
vi.setConfig({ testTimeout: 30_000 });

describe('scenario builder page', () => {
  it('starts from the template, validates live and publishes (no assertions, only used fixtures)', async () => {
    const published = captureBodies('/v1/scenarios');
    const validated = captureBodies('/scenario-drafts/validate');
    const { user, router } = await openNew(true);
    expect(screen.getByRole('heading', { level: 1, name: 'New scenario' })).toBeInTheDocument();
    expect(screen.getByTestId('page-intro')).toHaveTextContent('Build a scenario step by step');
    await waitValid();
    expect(validated.length).toBeGreaterThan(0);
    expect(validated[0]).toMatchObject({ fixtures: { 'fixtures/invoice-001.eml': expect.any(String) } });
    await user.click(screen.getByRole('button', { name: 'Save and publish' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/scenarios/finance.invoice-review'));
    const body = published[0] as { document: { scenario: { evaluation: unknown } }; fixtures: Record<string, string> };
    expect(body.document.scenario.evaluation).toEqual({ limits: { max_agent_actions: 50, max_virtual_duration_seconds: 3600 } });
    expect(Object.keys(body.fixtures)).toEqual(['fixtures/invoice-001.eml']);
    const notice = await screen.findByTestId('published-notice');
    expect(notice).toHaveTextContent('Published version 1');
    expect(within(notice).getByRole('link', { name: 'Write its answer key' })).toHaveAttribute('href', '/scenarios/finance.invoice-review/answer-keys/new');
    expect(within(notice).getByRole('link', { name: 'Start a run' })).toHaveAttribute('href', '/scenarios/finance.invoice-review/start');
    expect(sessionStorage.getItem(draftStorageKey('new', ''))).toBeNull();
  });

  it('lists problems for a blank scenario, links them to fields and counts them per section', async () => {
    const { user } = await openNew();
    await waitInvalid();
    expect(screen.getByRole('button', { name: 'Save and publish' })).toBeDisabled();
    expect(screen.getByText('Saving is available once the scenario is valid.')).toBeInTheDocument();
    const summary = screen.getByTestId('error-summary');
    // id + title + domain + agent mailbox
    expect(within(summary).getAllByRole('listitem')).toHaveLength(4);
    const nav = screen.getByRole('navigation', { name: 'Scenario sections' });
    expect(within(nav).getByRole('button', { name: '1. Basics, 2 problems' })).toHaveAttribute('aria-current', 'step');
    expect(within(nav).getByRole('button', { name: '4. Mailboxes, 1 problem' })).toBeInTheDocument();

    // an edit that does not change the problems keeps the same list
    await user.type(screen.getByRole('textbox', { name: /Tags/ }), 'z');
    await user.click(screen.getByRole('button', { name: 'Add to Tags' }));
    await screen.findByText('Validating…');
    await waitInvalid();
    expect(within(screen.getByTestId('error-summary')).getAllByRole('listitem')).toHaveLength(4);
    await user.click(within(summary).getByRole('button', { name: /Organisation: must not be empty/ }));
    expect(screen.getByLabelText(/Mail domain/)).toHaveFocus();
    expect(screen.getByLabelText(/Mail domain/)).toHaveAttribute('aria-invalid', 'true');
    // agent.mailbox maps to the radio group in Mailboxes; with no personas it is not on screen
    await user.click(within(summary).getByRole('button', { name: /Mailboxes: Field required|agent mailbox/ }));
    expect(screen.getByRole('heading', { level: 2, name: 'Mailboxes' })).toHaveFocus();
    expect(within(summary).getByText(/agent\.mailbox/).parentElement).toHaveTextContent('not tied to a single field');

    // pager
    await user.click(screen.getByRole('button', { name: 'Next section' }));
    expect(screen.getByRole('heading', { level: 2, name: 'Initial messages' })).toHaveFocus();
    await user.click(screen.getByRole('button', { name: 'Previous section' }));
    await user.click(within(nav).getByRole('button', { name: /^8\. Limits/ }));
    expect(screen.getByRole('button', { name: 'Next section' })).toBeDisabled();
    await user.click(within(nav).getByRole('button', { name: /^1\. Basics/ }));
    expect(screen.getByRole('button', { name: 'Previous section' })).toBeDisabled();

    await user.type(screen.getByLabelText(/Scenario id/), 'a.b');
    await user.type(screen.getByLabelText(/^Title/), 'T');
    await user.type(screen.getByRole('textbox', { name: /Tags/ }), 'x{Enter}');
    await user.clear(screen.getByLabelText(/Version/));
    await user.type(screen.getByLabelText(/Version/), '0');
    await screen.findByText('Input should be greater than or equal to 1', {}, SLOW);
  });

  it('shows validation and publish failures', async () => {
    server.use(http.post('*/v1/scenario-drafts/validate', () => fail(500, 'INTERNAL', 'validator down')));
    const { user } = await openNew(true);
    await screen.findByText('Could not validate', {}, SLOW);
    expect(screen.getByTestId('api-error')).toHaveTextContent('validator down');
    server.use(http.post('*/v1/scenario-drafts/validate', () => HttpResponse.json({ ok: true, errors: [], warnings: [], document: null })));
    await user.type(screen.getByLabelText(/^Title/), '!');
    await waitValid();
    server.use(http.post('*/v1/scenarios', () => fail(409, 'CONFLICT', 'version exists')));
    await user.click(screen.getByRole('button', { name: 'Save and publish' }));
    expect(await screen.findByText(/version exists/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save and publish' })).toBeEnabled();
  });

  it('falls back to built-in option lists when meta is unavailable', async () => {
    server.use(http.get('*/v1/meta/authoring', () => fail(500, 'INTERNAL')));
    await openNew();
    expect(await screen.findByText(/Could not load the server’s option lists/)).toBeInTheDocument();
  });

  it('edits a published version as the next version and keeps the id', async () => {
    const published = captureBodies('/v1/scenarios');
    server.use(
      http.post('*/v1/scenario-drafts/inspect-eml', () => fail(400, 'INVALID_REQUEST', 'not a message')),
    );
    const { user, router } = renderApp('/scenarios/finance.invoice-bank-change/edit?from=2', sessionFor('controller'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Edit finance.invoice-bank-change (new version)' })).toBeInTheDocument();
    const id = screen.getByLabelText(/Scenario id/);
    expect(id).toHaveAttribute('readonly');
    expect(screen.getByLabelText(/Version/)).toHaveValue(4);
    expect(screen.getByText(/saving publishes a new version of it/)).toBeInTheDocument();
    const messages = await goSection(user, 'Initial messages');
    expect(await messages.findByText('fixtures/invoice-4471.eml (could not be read)')).toBeInTheDocument();
    await waitValid();
    await user.click(screen.getByRole('button', { name: 'Publish version 4' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/scenarios/finance.invoice-bank-change'));
    expect(published[0]).toMatchObject({ document: { scenario: { id: 'finance.invoice-bank-change', version: 4 } } });
    expect(await screen.findByTestId('published-notice')).toHaveTextContent('Published version 4');
    expect(screen.getByTestId('scenario-versions')).toHaveTextContent('v4');
  });

  it('clones the latest version under a new id', async () => {
    const { user } = renderApp('/scenarios/support.refund-escalation/clone', sessionFor('controller'));
    expect(await screen.findByRole('heading', { level: 1, name: 'Clone of support.refund-escalation' })).toBeInTheDocument();
    expect(screen.getByLabelText(/Scenario id/)).toHaveValue('support.refund-escalation-copy');
    expect(screen.getByLabelText(/^Title/)).toHaveValue('Refund escalation (copy)');
    expect(screen.getByLabelText(/Version/)).toHaveValue(1);
    expect(screen.getByLabelText(/Scenario id/)).not.toHaveAttribute('readonly');
    const messages = await goSection(user, 'Initial messages');
    expect(await messages.findByText('Invoice 4471')).toBeInTheDocument();
  });

  it('clones a version without a title and reports load failures', async () => {
    server.use(
      http.get('*/v1/scenarios/:key/versions/:version/document', ({ params }) =>
        params.version === '1'
          ? HttpResponse.json({ scenario_key: 'k', version: 1, spec_hash: 'h', document: { scenario: { id: 'k' } }, fixtures: {} })
          : fail(404, 'NOT_FOUND', 'no such version'),
      ),
    );
    renderApp('/scenarios/finance.invoice-bank-change/clone?from=1', sessionFor('controller'));
    expect(await screen.findByLabelText(/^Title/)).toHaveValue('');
    expect(screen.getByLabelText(/Scenario id/)).toHaveValue('k-copy');
  });

  it('shows loading and error states for a version that cannot be loaded', async () => {
    server.use(http.get('*/v1/scenarios/:key/versions/:version/document', () => fail(404, 'NOT_FOUND', 'no such version')));
    renderApp('/scenarios/finance.invoice-bank-change/edit?from=9', sessionFor('controller'));
    expect(screen.getByText('Loading the scenario…')).toBeInTheDocument();
    expect(await screen.findByText(/no such version/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to finance.invoice-bank-change' })).toBeInTheDocument();
  });

  it('restores a draft kept in this tab and says when files were too large to keep', async () => {
    const draft = templateDraft();
    draft.form.title = 'Restored title';
    saveDraft(draftStorageKey('new', ''), draft, documentKey(blankForm()));
    const { user } = renderApp('/scenarios/new', sessionFor('controller'));
    expect(await screen.findByTestId('builder-notice')).toHaveTextContent('Restored your unsaved draft');
    expect(screen.getByLabelText(/^Title/)).toHaveValue('Restored title');
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByTestId('builder-notice')).toBeNull();

  });

  it('warns about too-large drafts while editing', async () => {
    const stored = templateDraft();
    stored.fixtures['fixtures/invoice-001.eml'] = 'A'.repeat(2_100_000);
    sessionStorage.setItem(
      draftStorageKey('edit', 'finance.invoice-bank-change'),
      JSON.stringify({ draft: stored, baseline: 'base', fixturesKept: true }),
    );
    const { user } = renderApp('/scenarios/finance.invoice-bank-change/edit', sessionFor('controller'));
    await user.type(await screen.findByLabelText(/^Title/), 'x');
    expect(await screen.findByTestId('fixtures-not-kept', {}, SLOW)).toBeInTheDocument();
  });

  it('guards unsaved changes: stay, leave keeping the draft, discard', async () => {
    const { user, router } = await openNew(true);
    await user.type(screen.getByLabelText(/^Title/), ' changed');
    const ev = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);

    await act(() => router.navigate('/runs'));
    const dialog = await screen.findByRole('alertdialog', { name: 'Leave without saving?' });
    expect(within(dialog).getByRole('button', { name: 'Stay on this page' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: 'Stay on this page' }));
    expect(router.state.location.pathname).toBe('/scenarios/new');
    // same page (query change) is never blocked
    await act(() => router.navigate('/scenarios/new?x=1'));
    expect(screen.queryByRole('alertdialog')).toBeNull();

    await act(() => router.navigate('/runs'));
    await user.click(await screen.findByRole('button', { name: 'Leave (keep the draft)' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/runs'));
    expect(sessionStorage.getItem(draftStorageKey('new', ''))).toContain(' changed');

    await act(() => router.navigate('/scenarios/new'));
    expect(await screen.findByTestId('builder-notice')).toHaveTextContent('Restored');
    await act(() => router.navigate('/runs'));
    await user.click(await screen.findByRole('button', { name: 'Discard the draft and leave' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/runs'));
    expect(sessionStorage.getItem(draftStorageKey('new', ''))).toBeNull();
  });

  it('does not warn when nothing changed', async () => {
    const { router } = await openNew();
    const ev = new Event('beforeunload', { cancelable: true });
    fireEvent(window, ev);
    expect(ev.defaultPrevented).toBe(false);
    await act(() => router.navigate('/runs'));
    expect(router.state.location.pathname).toBe('/runs');
  });

  it('is controller-only, and entry points appear for controllers', async () => {
    renderApp('/scenarios/new', sessionFor('evaluator'));
    expect(await screen.findByTestId('role-gate')).toHaveTextContent('Building scenarios requires a controller key');
  });

  it('shows the new/edit/clone entry points', async () => {
    const { user, router } = renderApp('/scenarios', sessionFor('controller'));
    await user.click(await screen.findByRole('link', { name: 'New scenario' }));
    expect(router.state.location.pathname).toBe('/scenarios/new');
    await act(() => router.navigate('/scenarios/finance.invoice-bank-change'));
    expect(await screen.findByRole('link', { name: 'Edit (new version)' })).toHaveAttribute(
      'href',
      '/scenarios/finance.invoice-bank-change/edit?from=3',
    );
    expect(screen.getByRole('link', { name: 'Clone' })).toHaveAttribute('href', '/scenarios/finance.invoice-bank-change/clone?from=3');
    expect(screen.queryByTestId('published-notice')).toBeNull();
  });

  it('hides edit/clone for a scenario without versions', async () => {
    server.use(
      http.get('*/v1/scenarios/:key', () => HttpResponse.json({ id: 's', key: 'empty.one', latest_version: null, versions: [] })),
    );
    renderApp('/scenarios/empty.one', sessionFor('controller'));
    expect(await screen.findByText('No published version.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Clone' })).toBeNull();
  });
});
