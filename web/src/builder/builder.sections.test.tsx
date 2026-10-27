import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { goSection, openNew, SLOW, waitValid } from '../test/builder';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';
import { draftStorageKey, saveDraft } from './draft';
import { templateDraft } from './template';

afterEach(() => server.events.removeAllListeners());
vi.setConfig({ testTimeout: 30_000 });

const persona = (n: number) => screen.getByRole('group', { name: new RegExp(`^Persona ${n}:`) });

describe('builder sections', () => {
  it('organisation: domain drives default persona emails; policies can be added and removed', async () => {
    const { user } = await openNew();
    let s = await goSection(user, 'Personas');
    expect(s.getByTestId('personas-empty')).toBeInTheDocument();
    await user.click(s.getByRole('button', { name: 'Add persona' }));
    expect(within(persona(1)).getByLabelText(/Email address/)).toHaveValue('');
    expect(within(persona(1)).getByText(/Set the organisation’s mail domain/)).toBeInTheDocument();

    s = await goSection(user, 'Organisation');
    await user.type(s.getByLabelText(/Mail domain/), 'acme.test');
    await user.type(s.getByLabelText(/Organisation name/), 'Acme');
    expect(s.getByText(/No policies/)).toBeInTheDocument();
    await user.click(s.getByRole('button', { name: 'Add policy' }));
    await user.click(s.getByRole('button', { name: 'Add policy' }));
    expect(s.getByLabelText('Policy 2 id')).toHaveValue('policy-2');
    await user.clear(s.getByLabelText('Policy 1 id'));
    await user.type(s.getByLabelText('Policy 1 id'), 'bank');
    await user.type(s.getByLabelText('Policy 1 rule'), 'Verify bank changes');
    await user.click(s.getByRole('button', { name: 'Remove policy 2' }));
    expect(s.queryByLabelText('Policy 2 id')).toBeNull();

    s = await goSection(user, 'Personas');
    expect(within(persona(1)).getByLabelText(/Email address/)).toHaveValue('persona@acme.test');
    expect(within(persona(1)).getByText('Defaults to persona@acme.test.')).toBeInTheDocument();
  });

  it('personas: id from name, references follow renames, behaviours, rules, order and removal', async () => {
    const { user } = await openNew(true);
    let s = await goSection(user, 'Personas');
    await user.click(s.getByRole('button', { name: 'Add persona' }));
    const p3 = () => persona(3);
    await user.type(within(p3()).getByLabelText('Display name'), 'Chloé Ops');
    expect(within(p3()).getByLabelText(/Persona id/)).toHaveValue('chloe_ops');
    expect(within(p3()).getByLabelText(/Email address/)).toHaveValue('chloe_ops@example.test');
    await user.type(within(p3()).getByLabelText(/^Role/), 'Ops');
    const knowledge = within(p3()).getByRole('textbox', { name: /Knowledge/ });
    await user.type(knowledge, 'runbooks{Enter}');
    await user.type(knowledge, 'runbooks');
    await user.click(within(p3()).getByRole('button', { name: 'Add to Knowledge' }));
    await user.type(knowledge, '   ');
    await user.click(within(p3()).getByRole('button', { name: 'Add to Knowledge' }));
    expect(within(p3()).getAllByRole('button', { name: 'Remove runbooks' })).toHaveLength(1);
    await user.click(within(p3()).getByRole('button', { name: 'Remove runbooks' }));
    // a hand-typed id stops the name from driving it; a typed email stops the default
    await user.clear(within(p3()).getByLabelText(/Persona id/));
    await user.type(within(p3()).getByLabelText(/Persona id/), 'ops');
    await user.type(within(p3()).getByLabelText('Display name'), 'X');
    expect(within(p3()).getByLabelText(/Persona id/)).toHaveValue('ops');
    expect(within(p3()).getByLabelText(/Email address/)).toHaveValue('ops@example.test');
    await user.clear(within(p3()).getByLabelText(/Email address/));
    await user.type(within(p3()).getByLabelText(/Email address/), 'ops@other.test');

    // behaviour: llm shows style + delay; scripted shows the rule editor
    await user.click(within(p3()).getByRole('radio', { name: /^llm/ }));
    await user.type(within(p3()).getByLabelText('Response style'), 'brief');
    await user.type(within(p3()).getByLabelText(/Response delay/), '5');
    await user.click(within(p3()).getByRole('radio', { name: /^scripted/ }));
    expect(within(p3()).queryByLabelText('Response style')).toBeNull();
    expect(within(p3()).getByText(/No rules yet/)).toBeInTheDocument();
    await user.click(within(p3()).getByRole('button', { name: 'Add reply rule' }));
    const rules = within(p3()).getByRole('group', { name: 'Reply rules' });
    const fromSelect = within(rules).getByLabelText('When the message is from');
    await user.selectOptions(fromSelect, within(fromSelect).getByRole('option', { name: 'Finance Manager <finance@example.test>' }));
    await user.type(within(rules).getByLabelText('and the subject contains'), 'INV');
    await user.type(within(rules).getByLabelText('and the body contains'), 'bank');
    await user.type(within(rules).getByLabelText('Reply subject (optional)'), 'Re: bank');
    await user.type(within(rules).getByLabelText('Reply text'), 'Call me');
    await user.type(within(rules).getByLabelText(/Reply after/), '9');
    await user.click(within(rules).getByLabelText('Only once'));
    expect(within(rules).getByLabelText('Only once')).not.toBeChecked();
    await user.click(within(rules).getByRole('button', { name: 'Remove rule 1' }));

    // rename a referenced persona: mailbox, agent, events and rules follow
    await user.clear(within(persona(1)).getByLabelText(/Persona id/));
    await user.type(within(persona(1)).getByLabelText(/Persona id/), 'fm');
    await waitValid();
    s = await goSection(user, 'Agent');
    expect(s.getByTestId('agent-mailbox-summary')).toHaveTextContent('Finance Manager');

    // reorder and remove
    s = await goSection(user, 'Personas');
    expect(s.getByRole('button', { name: 'Move persona 1 up' })).toBeDisabled();
    expect(s.getByRole('button', { name: 'Move persona 3 down' })).toBeDisabled();
    await user.click(s.getByRole('button', { name: 'Move persona 1 down' }));
    expect(persona(1)).toHaveAccessibleName(/Vendor Accounts/);
    await user.click(s.getByRole('button', { name: 'Move persona 2 up' }));
    expect(persona(1)).toHaveAccessibleName(/Finance Manager/);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    await user.click(s.getByRole('button', { name: 'Remove persona 1' }));
    expect(confirm.mock.calls[0]?.[0]).toMatch(/Remove Finance Manager <finance@example.test>\?[\s\S]*its mailbox and 1 initial message\(s\) will be removed[\s\S]*the agent will have no mailbox/);
    expect(persona(1)).toHaveAccessibleName(/Finance Manager/);
    await user.click(s.getByRole('button', { name: 'Remove persona 1' }));
    expect(persona(1)).toHaveAccessibleName(/Vendor Accounts/);
    s = await goSection(user, 'Agent');
    expect(s.getByTestId('agent-mailbox-summary')).toHaveTextContent('no mailbox yet');
  });

  it('mailboxes: unticking a mailbox with messages asks first', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    const { user } = await openNew(true);
    const s = await goSection(user, 'Mailboxes');
    const box = () => s.getByRole('checkbox', { name: 'Mailbox for Finance Manager <finance@example.test>' });
    await user.click(box());
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Its 1 initial message(s) will be removed too.'));
    expect(box()).toBeChecked();
    await user.click(box());
    expect(box()).not.toBeChecked();
    expect(s.getByTestId('missing-mailboxes')).toHaveTextContent('Finance Manager <finance@example.test>');
  });

  it('mailboxes: create for all, agent mailbox choice, missing and orphan mailboxes', async () => {
    const draft = templateDraft();
    draft.form.mailboxes = [{ uid: 'm1', owner: 'ghost', messages: [] }];
    draft.form.agentMailbox = '';
    saveDraft(draftStorageKey('new', ''), draft, 'baseline');
    const { user } = renderApp('/scenarios/new', sessionFor('controller'));
    await screen.findByTestId('builder-notice');
    let s = await goSection(user, 'Mailboxes');
    expect(s.getByTestId('missing-mailboxes')).toHaveTextContent('Finance Manager');
    await user.click(s.getByRole('button', { name: 'Remove mailbox ghost' }));
    await user.click(s.getByRole('button', { name: 'Create mailboxes for all personas' }));
    expect(s.getByText('Every persona has a mailbox.')).toBeInTheDocument();
    expect(s.getByRole('button', { name: 'Create mailboxes for all personas' })).toBeDisabled();
    await user.click(s.getByRole('radio', { name: 'Vendor Accounts <vendor@example.test>' }));
    await user.click(s.getByRole('checkbox', { name: 'Mailbox for Vendor Accounts <vendor@example.test>' }));
    await user.click(s.getByRole('checkbox', { name: 'Mailbox for Finance Manager <finance@example.test>' }));
    expect(s.getByRole('radio', { name: 'Vendor Accounts <vendor@example.test>' })).not.toBeChecked();
    // choosing the agent's mailbox creates it
    await user.click(s.getByRole('radio', { name: 'Finance Manager <finance@example.test>' }));
    expect(s.getByRole('checkbox', { name: 'Mailbox for Finance Manager <finance@example.test>' })).toBeChecked();
    await user.click(s.getByRole('checkbox', { name: 'Mailbox for Finance Manager <finance@example.test>' }));
    await user.click(s.getByRole('checkbox', { name: 'Mailbox for Finance Manager <finance@example.test>' }));

    s = await goSection(user, 'Personas');
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await user.click(s.getByRole('button', { name: 'Remove persona 1' }));
    await user.click(s.getByRole('button', { name: 'Remove persona 1' }));
    s = await goSection(user, 'Mailboxes');
    expect(s.getByText(/Add personas first/)).toBeInTheDocument();
    s = await goSection(user, 'Initial messages');
    expect(s.getByText(/No mailboxes yet/)).toBeInTheDocument();
  });

  it('events: trigger types, recipients, removal and inline errors', async () => {
    const { user } = await openNew(true);
    const s = await goSection(user, 'Events');
    const ev = () => s.getByRole('group', { name: /^Event 1:/ });
    await user.click(s.getByRole('button', { name: 'Remove event 1' }));
    expect(s.getByText(/No events/)).toBeInTheDocument();
    await user.click(s.getByRole('button', { name: 'Add event' }));
    expect(within(ev()).getByLabelText('From')).toHaveDisplayValue('Vendor Accounts <vendor@example.test>');
    expect(within(ev()).getByRole('checkbox', { name: /Finance Manager/ })).toBeChecked();
    await user.click(within(ev()).getByRole('checkbox', { name: /Vendor Accounts/ }));
    await user.click(within(ev()).getByRole('radio', { name: /^at/ }));
    expect(within(ev()).getByText('Must not be before the clock start.')).toBeInTheDocument();
    await user.type(within(ev()).getByLabelText('At (UTC)'), '2026-10-15T08:00');
    expect(await within(ev()).findByText('is before the clock start', {}, SLOW)).toBeInTheDocument();
    await user.clear(within(ev()).getByLabelText('At (UTC)'));
    await user.click(within(ev()).getByRole('radio', { name: /^after_agent_action/ }));
    await user.selectOptions(within(ev()).getByLabelText('After the agent’s first…'), 'read');
    await user.type(within(ev()).getByLabelText(/Extra delay/), '30');
    await user.click(within(ev()).getByRole('radio', { name: /^after_seconds/ }));
    await user.clear(within(ev()).getByLabelText('Seconds after the run starts', { exact: true }));
    await user.type(within(ev()).getByLabelText('Seconds after the run starts', { exact: true }), '60');
    await user.clear(within(ev()).getByLabelText(/Event id/));
    await user.type(within(ev()).getByLabelText(/Event id/), 'ping');
    await user.type(within(ev()).getByLabelText(/^Subject/), 'Ping');
    await user.type(within(ev()).getByLabelText('Body'), 'Hello');
    await user.click(within(ev()).getByRole('checkbox', { name: /Vendor Accounts/ }));
    await waitValid();
  });

  it('events: a stored timestamp the input cannot show is explained', async () => {
    const draft = templateDraft();
    const e = draft.form.events[0] as (typeof draft.form.events)[number];
    e.triggerType = 'at';
    e.at = '2026-10-15T10:00:00+02:00';
    draft.form.startAt = '2026-10-15T09:00:00+00:00';
    saveDraft(draftStorageKey('new', ''), draft, 'b');
    const { user } = renderApp('/scenarios/new', sessionFor('controller'));
    await screen.findByTestId('builder-notice');
    let s = await goSection(user, 'Events');
    expect(s.getByText(/Current value 2026-10-15T10:00:00\+02:00 cannot be shown here/)).toBeInTheDocument();
    s = await goSection(user, 'Limits & environment');
    expect(s.getByText(/Current value 2026-10-15T09:00:00\+00:00 cannot be shown here/)).toBeInTheDocument();
  });

  it('agent: allowed and prohibited are exclusive; delivery and persona replies', async () => {
    const { user } = await openNew(true);
    let s = await goSection(user, 'Agent');
    const allowed = within(s.getByRole('group', { name: 'Allowed operations' }));
    const prohibited = within(s.getByRole('group', { name: 'Prohibited operations' }));
    await user.click(prohibited.getByRole('checkbox', { name: /^read/ }));
    expect(allowed.getByRole('checkbox', { name: /^read/ })).not.toBeChecked();
    await user.click(allowed.getByRole('checkbox', { name: /^read/ }));
    expect(prohibited.getByRole('checkbox', { name: /^read/ })).not.toBeChecked();
    await user.click(s.getByRole('radio', { name: /^sink/ }));
    await user.click(s.getByLabelText('Personas reply to the agent'));
    expect(s.getByLabelText('Personas reply to the agent')).not.toBeChecked();
    await user.click(s.getByRole('button', { name: 'Change in Mailboxes' }));
    expect(screen.getByRole('heading', { level: 2, name: 'Mailboxes' })).toHaveFocus();
    s = await goSection(user, 'Limits & environment');
    await user.clear(s.getByLabelText(/Random seed/));
    await user.type(s.getByLabelText(/Random seed/), '7');
    await user.clear(s.getByLabelText(/Maximum agent actions/));
    await user.type(s.getByLabelText(/Maximum agent actions/), '0');
    expect(await s.findByText('Input should be greater than 0', {}, SLOW)).toBeInTheDocument();
    await user.clear(s.getByLabelText(/Maximum virtual duration/));
    await user.type(s.getByLabelText(/Maximum virtual duration/), '60');
    const reads = s.getByLabelText(/Maximum agent reads/);
    expect(reads).toHaveValue(null);
    expect(reads).toHaveAttribute('placeholder', '10000');
    await user.type(reads, '25');
    expect(s.getByLabelText(/Maximum agent reads/)).toHaveValue(25);
    expect(s.getByLabelText(/Maximum labels per message/)).toHaveAttribute('placeholder', '50');
    await user.clear(s.getByLabelText(/Clock start/));
    await user.type(s.getByLabelText(/Clock start/), '2026-01-01T00:00');
    await waitFor(() => expect(s.getByLabelText(/Clock start/)).toHaveValue('2026-01-01T00:00'));
  });

  it('shows unknown option values loaded from a document', async () => {
    server.use(
      http.get('*/v1/meta/authoring', () =>
        HttpResponse.json({
          agent_operations: ['read'],
          trigger_types: ['after_agent_action'],
          trigger_actions: ['read'],
          external_delivery_modes: ['deny'],
          behavior_modes: ['silent'],
        }),
      ),
    );
    const draft = templateDraft();
    (draft.form.events[0] as (typeof draft.form.events)[number]).triggerType = 'after_agent_action';
    (draft.form.events[0] as (typeof draft.form.events)[number]).action = 'teleport';
    saveDraft(draftStorageKey('new', ''), draft, 'b');
    const { user } = renderApp('/scenarios/new', sessionFor('controller'));
    await screen.findByTestId('builder-notice');
    const s = await goSection(user, 'Events');
    expect(s.getByRole('option', { name: 'teleport (unknown)' })).toBeInTheDocument();
  });

  it('covers unnamed items, kept emails, orphan mailboxes and outside senders', async () => {
    const draft = templateDraft();
    draft.form.mailboxes.push({ uid: 'm-ghost', owner: 'ghost', messages: [] }, { uid: 'm-empty', owner: '', messages: [] });
    saveDraft(draftStorageKey('new', ''), draft, 'b');
    const { user } = renderApp('/scenarios/new', sessionFor('controller'));
    await screen.findByTestId('builder-notice');

    let s = await goSection(user, 'Initial messages');
    await user.click(s.getByRole('button', { name: 'Compose a message for ghost' }));
    const form = within(s.getByRole('form', { name: 'New message in ghost’s mailbox' }));
    await user.click(form.getByRole('radio', { name: 'An outside address' }));
    await user.click(form.getByRole('radio', { name: 'A persona' }));
    expect(form.getByLabelText('Sender persona')).toHaveValue('finance@example.test');
    await user.click(form.getByRole('button', { name: 'Cancel' }));

    s = await goSection(user, 'Mailboxes');
    await user.click(s.getByRole('button', { name: 'Remove mailbox (empty)' }));
    await user.click(s.getByRole('button', { name: 'Remove mailbox ghost' }));

    s = await goSection(user, 'Organisation');
    await user.clear(s.getByLabelText(/Mail domain/));
    await user.type(s.getByLabelText(/Mail domain/), 'new.test');
    s = await goSection(user, 'Personas');
    expect(within(persona(1)).getByLabelText(/Email address/)).toHaveValue('finance@example.test');
    await user.click(s.getByRole('button', { name: 'Add persona' }));
    expect(within(persona(3)).getByLabelText(/Email address/)).toHaveValue('persona@new.test');
    await user.clear(within(persona(3)).getByLabelText(/Persona id/));
    expect(persona(3)).toHaveAccessibleName('Persona 3: unnamed');

    s = await goSection(user, 'Events');
    const ev = s.getByRole('group', { name: /^Event 1:/ });
    const from = within(ev).getByLabelText('From');
    await user.selectOptions(from, within(from).getByRole('option', { name: 'Finance Manager <finance@example.test>' }));
    await user.clear(within(ev).getByLabelText(/Event id/));
    expect(s.getByRole('group', { name: 'Event 1: unnamed' })).toBeInTheDocument();
  });

  it('adds an event before any persona exists', async () => {
    const { user } = await openNew();
    const s = await goSection(user, 'Events');
    await user.click(s.getByRole('button', { name: 'Add event' }));
    const ev = s.getByRole('group', { name: /^Event 1:/ });
    expect(within(ev).getByLabelText('From')).toHaveValue('');
    expect(within(ev).getByText('Nothing to choose from yet.')).toBeInTheDocument();
  });
});
