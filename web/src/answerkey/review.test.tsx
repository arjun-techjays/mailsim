// Regression tests for the UX-F2 review findings (draft storage, pickers, defaults, YAML).
import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { mockDb, server } from '../test/server';
import { OPERATOR_SESSION, renderApp, sessionFor } from '../test/utils';
import { draftKey } from './draftStorage';
import { droppedKeys, emptyDraft, newAssertion } from './model';
import { AUTHORING_META } from '../mocks/authoring';
import { clientIssues, leakedFields } from './validation';

const KEY = 'finance.invoice-bank-change';
const NEW = `/scenarios/${KEY}/answer-key`;
const drafts = () => Object.keys(sessionStorage).filter((k) => k.startsWith('mailsim.answerKeyDraft'));
const cards = () => Array.from(document.querySelectorAll<HTMLElement>('.ak-card'));
const card = (i: number) => within(cards()[i] as HTMLElement);
const settle = () =>
  waitFor(() => expect(screen.getByTestId('validation-status')).not.toHaveTextContent(/checking/i), { timeout: 3000 });

describe('answer-key drafts (security)', () => {
  it('opening an existing key without editing stores nothing; Cancel clears an edited draft', { timeout: 30_000 }, async () => {
    const { user } = renderApp(`${NEW}?from=2`, sessionFor('evaluator'));
    await screen.findByText(/Editing a copy of answer key v2/);
    await settle();
    expect(drafts()).toEqual([]);
    expect(screen.queryByTestId('draft-restored')).toBeNull();
    await user.type(card(0).getByLabelText(/Requirement/), ' now');
    await waitFor(() => expect(drafts()).toEqual([draftKey(KEY, 2)]));
    await user.click(screen.getByRole('link', { name: 'Cancel (discard the draft)' }));
    expect(await screen.findByRole('heading', { name: /Scenario/ })).toBeInTheDocument();
    expect(drafts()).toEqual([]);
  });

  it('discarding leaves no stored key, and disconnecting removes every draft', { timeout: 30_000 }, async () => {
    sessionStorage.setItem(draftKey(KEY, null), JSON.stringify(emptyDraft(KEY, 8)));
    sessionStorage.setItem(draftKey('other', 1), JSON.stringify(emptyDraft('other', 2)));
    const { user } = renderApp(NEW, sessionFor('evaluator'));
    await user.click(await screen.findByRole('button', { name: 'Discard draft and start over' }));
    await settle();
    expect(drafts()).toEqual([draftKey('other', 1)]);
    await user.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(drafts()).toEqual([]);
  });

  it('connecting with another key clears drafts too', async () => {
    sessionStorage.setItem(draftKey(KEY, null), JSON.stringify(emptyDraft(KEY, 8)));
    const { user } = renderApp('/connect', null);
    await user.type(await screen.findByLabelText('API key'), 'mk_evaluator');
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByTestId('detected-role');
    expect(drafts()).toEqual([]);
  });
});

describe('pickers and defaults', () => {
  it('from/to offer persona emails, labels come from the run, temporal is seeded with a scenario event', { timeout: 30_000 }, async () => {
    mockDb.localNoAuth = true;
    mockDb.runs.run_empty!.messages[0]!.labels = ['APPROVED']; // the newest run of the scenario
    const { user } = renderApp(NEW, OPERATOR_SESSION);
    await screen.findByText(/No assertions yet/);
    await user.click(screen.getByRole('button', { name: 'Add assertion' }));
    const to = card(0).getByLabelText('To');
    expect(card(0).getByText(/Email address, or part of a recipient’s address or name/)).toBeInTheDocument();
    const list = document.getElementById(to.getAttribute('list') as string) as HTMLElement;
    await waitFor(() => expect(list.querySelectorAll('option').length).toBe(3));
    const values = Array.from(list.querySelectorAll('option')).map((o) => o.getAttribute('value'));
    expect(values).toEqual(['finance@acme.test', 'billing@supplier.test', 'ceo@acme.test']);
    expect(list).toHaveTextContent('Asha Rao, Finance Manager (per_finance)');
    const label = card(0).getByLabelText('Label');
    await waitFor(() =>
      expect(document.getElementById(label.getAttribute('list') as string)?.querySelector('option')).toHaveAttribute('value', 'APPROVED'),
    );

    await user.selectOptions(screen.getByLabelText('New assertion type'), 'temporal');
    await user.click(screen.getByRole('button', { name: 'Add assertion' }));
    expect(card(1).getByRole('group', { name: 'First' })).toBeInTheDocument();
    expect(within(card(1).getByRole('group', { name: 'First' })).getByLabelText('Event')).toHaveValue('vendor-follow-up');
    expect(card(1).getAllByText(/exactly one/i).length).toBeGreaterThan(0);

    await user.selectOptions(screen.getByLabelText('New assertion type'), 'protocol');
    await user.click(screen.getByRole('button', { name: 'Add assertion' }));
    expect(card(2).getByText('Not set: the default is INVALID_REQUEST, UNSUPPORTED_OPERATION, NOT_FOUND.')).toBeInTheDocument();
  });

  it('content without filters explains the server default; emptied lists are dropped', { timeout: 30_000 }, async () => {
    const { user } = renderApp(NEW, sessionFor('evaluator'));
    await screen.findByText(/No assertions yet/);
    await user.selectOptions(screen.getByLabelText('New assertion type'), 'content');
    await user.click(screen.getByRole('button', { name: 'Add assertion' }));
    await user.selectOptions(card(0).getByLabelText('Folder'), '');
    expect(card(0).getByText(/Nothing set: the server looks at messages in the agent’s Sent folder/)).toBeInTheDocument();
    await user.click(card(0).getByRole('button', { name: 'Add to contains' }));
    await user.click(card(0).getByRole('button', { name: 'Remove contains 1' }));
    await user.click(screen.getByRole('button', { name: 'YAML' }));
    const yaml = (await screen.findByLabelText('Answer key as YAML')) as HTMLTextAreaElement;
    expect(yaml.value).not.toContain('contains');
  });

  it('YAML apply lists unknown fields that were dropped', { timeout: 30_000 }, async () => {
    const { user } = renderApp(`${NEW}?from=1`, sessionFor('evaluator'));
    await screen.findByText(/Editing a copy of answer key v1/);
    await user.click(screen.getByRole('button', { name: 'YAML' }));
    const yaml = (await screen.findByLabelText('Answer key as YAML')) as HTMLTextAreaElement;
    const text = yaml.value.replace('  checks_default: "on"', '  checks_default: "on"\n  notes: "x"');
    await user.clear(yaml);
    await user.click(yaml);
    await user.paste(text);
    await user.click(screen.getByRole('button', { name: 'Form' }));
    expect(await screen.findByTestId('yaml-dropped')).toHaveTextContent('answer_key.notes');
  });

  it('settled validation results are announced once, not every keystroke', { timeout: 30_000 }, async () => {
    const { user } = renderApp(NEW, sessionFor('evaluator'));
    await screen.findByText(/No assertions yet/);
    await settle();
    const announcer = screen.getByTestId('validation-announcer');
    await waitFor(() => expect(announcer).toHaveTextContent('No problems found. Ready to publish.'), { timeout: 3000 });
    await user.click(screen.getByRole('button', { name: 'Add assertion' }));
    expect(announcer).not.toHaveTextContent(/checking/i);
    await waitFor(() => expect(announcer).toHaveTextContent('1 problem to fix.'), { timeout: 5000 });
  });
});

describe('model helpers from the review', () => {
  it('flags dropped keys, blank evaluate-at events and long leaked texts', () => {
    expect(
      droppedKeys({ answer_key_version: '1.0', extra: 1, answer_key: { version: 1, foo: 2, assertions: [{ id: 'a', bar: 1 }, 'x'] } }),
    ).toEqual(['extra', 'answer_key.foo', 'answer_key.assertions.0.bar']);
    expect(droppedKeys({})).toEqual([]);
    const d = emptyDraft('s', 1);
    d.assertions = [{ ...newAssertion('state', AUTHORING_META, []), requirement: 'r', evaluateAt: 'after_event', evaluateEvent: ' ' }];
    expect(clientIssues(d).map((i) => i.path)).toEqual(['assertions.0.evaluate_at.event']);
    const long = 'a'.repeat(70) + ' tail';
    const a = { ...newAssertion('state', AUTHORING_META, []), requirement: long };
    expect(leakedFields([a], [{ path: '', message: `"${'a'.repeat(60)}…" leaks`, leakVersion: '1' }]).map((i) => i.path)).toEqual([
      'assertions.0.requirement',
    ]);
  });
});

describe('server error envelopes', () => {
  it('a publish refused by the server shows its field errors', { timeout: 30_000 }, async () => {
    server.use(
      http.post('*/v1/scenarios/:key/answer-keys', () =>
        HttpResponse.json(
          { error: { code: 'INVALID_REQUEST', message: 'bad', details: { errors: [{ loc: ['answer_key', 'version'], msg: 'taken' }] }, request_id: 'r' } },
          { status: 400 },
        ),
      ),
    );
    const { user } = renderApp(NEW, sessionFor('evaluator'));
    await screen.findByText(/No assertions yet/);
    await settle();
    await user.click(screen.getByRole('button', { name: 'Publish answer key v3' }));
    expect(await screen.findByText(/answer_key.version/)).toBeInTheDocument();
  });
});
