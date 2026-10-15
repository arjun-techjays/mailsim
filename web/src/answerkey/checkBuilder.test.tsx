import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { AUTHORING_META } from '../mocks/akeyMeta';
import { CheckBuilder, type Pickers } from './CheckBuilder';
import type { JsonSchema } from './schema';

const schemaOf = (type: string) => AUTHORING_META.assertion_types.find((t) => t.type === type)?.check_schema as JsonSchema;

const withPersonas: Pickers = {
  meta: AUTHORING_META,
  personas: [{ id: 'p1', email: 'p1@x', label: 'Pat, Boss' }],
  events: ['ev-1'],
};
const bare: Pickers = { meta: AUTHORING_META, personas: [], events: [] };

function Harness({
  schema,
  initial,
  ctx = withPersonas,
  errors = new Map(),
  onValue,
}: {
  schema: JsonSchema;
  initial: Record<string, unknown>;
  ctx?: Pickers;
  errors?: Map<string, string[]>;
  onValue: (v: Record<string, unknown>) => void;
}) {
  const [v, setV] = useState(initial);
  return (
    <CheckBuilder
      schema={schema}
      value={v}
      path={['c']}
      ctx={ctx}
      errors={errors}
      onChange={(next) => {
        setV(next);
        onValue(next);
      }}
    />
  );
}

function setup(schema: JsonSchema, initial: Record<string, unknown>, opts: { ctx?: Pickers; errors?: Map<string, string[]> } = {}) {
  let last: Record<string, unknown> = initial;
  const user = userEvent.setup();
  render(<Harness schema={schema} initial={initial} {...opts} onValue={(v) => (last = v)} />);
  return { user, value: () => last };
}

describe('CheckBuilder', () => {
  it('state: message filters with persona, folder, flag, address and tri-state pickers, and counts', async () => {
    const errors = new Map([
      ['c.where.folder', ['bad folder']],
      ['c.count', ['use either eq or min/max']],
      ['c', ['top-level problem']],
    ]);
    const { user, value } = setup(schemaOf('state'), { where: { folder: 'sent' }, count: { min: 1 } }, { errors });
    expect(screen.getByText('top-level problem')).toBeInTheDocument();
    const where = screen.getByRole('group', { name: 'Which messages' });
    expect(within(where).getByLabelText('Folder')).toHaveAttribute('aria-invalid', 'true');
    expect(within(where).getByText('bad folder')).toBeInTheDocument();

    await user.selectOptions(within(where).getByLabelText('Mailbox'), 'p1');
    await user.selectOptions(within(where).getByLabelText('Folder'), '');
    await user.selectOptions(within(where).getByLabelText('Has flag'), 'FLAGGED');
    await user.type(within(where).getByLabelText('To'), 'p1@x');
    await user.selectOptions(within(where).getByLabelText('Has attachment'), 'yes');
    await user.click(within(where).getByLabelText('Include agent created'));
    expect(value()).toEqual({
      where: { mailbox: 'p1', has_flag: 'FLAGGED', to: 'p1@x', has_attachment: true, include_agent_created: true },
      count: { min: 1 },
    });
    await user.selectOptions(within(where).getByLabelText('Has attachment'), 'no');
    expect((value().where as Record<string, unknown>).has_attachment).toBe(false);
    await user.selectOptions(within(where).getByLabelText('Has attachment'), '');
    await user.click(within(where).getByLabelText('Include agent created'));
    expect(value().where).toEqual({ mailbox: 'p1', has_flag: 'FLAGGED', to: 'p1@x' });

    const count = screen.getByRole('group', { name: 'Count' });
    expect(within(count).getByText('use either eq or min/max')).toBeInTheDocument();
    expect(within(count).getByLabelText('Expected number')).toHaveValue('min');
    await user.selectOptions(within(count).getByLabelText('Expected number'), 'eq');
    expect(value().count).toEqual({ eq: 1 });
    await user.clear(within(count).getByLabelText('Exactly'));
    expect(value().count).toEqual({ eq: 0 });
    await user.type(within(count).getByLabelText('Exactly'), '3');
    await user.selectOptions(within(count).getByLabelText('Expected number'), 'max');
    expect(value().count).toEqual({ max: 3 });
    await user.selectOptions(within(count).getByLabelText('Expected number'), 'range');
    expect(value().count).toEqual({ min: 3, max: 3 });
    await user.selectOptions(within(count).getByLabelText('Expected number'), 'min');
    expect(value().count).toEqual({ min: 3 });
    await user.selectOptions(within(count).getByLabelText('Expected number'), '');
    expect(value().count).toBeUndefined();
  });

  it('state without personas: free-text mailbox; unknown values are kept as custom options', async () => {
    const { user, value } = setup(schemaOf('state'), { where: { has_flag: 'WEIRD' } }, { ctx: bare });
    expect(screen.getByRole('option', { name: 'WEIRD (custom)' })).toBeInTheDocument();
    await user.type(screen.getByLabelText('Mailbox'), 'vendor');
    expect(value().where).toEqual({ has_flag: 'WEIRD', mailbox: 'vendor' });
  });

  it('action: operation picker, outcome modes, JSON input and numbers', async () => {
    const { user, value } = setup(schemaOf('action'), { operation: 'reply', count: { eq: 2 } });
    await user.selectOptions(screen.getByLabelText(/^Operation/), 'send');
    await user.selectOptions(screen.getByLabelText('Actor'), 'simulator');
    const outcome = screen.getByLabelText('Outcome');
    await user.selectOptions(outcome, 'code');
    expect(value().outcome).toBe('PERMISSION_DENIED');
    await user.clear(screen.getByLabelText('Error code'));
    await user.type(screen.getByLabelText('Error code'), 'NOT_FOUND');
    expect(value().outcome).toBe('NOT_FOUND');
    await user.selectOptions(outcome, 'any');
    expect(value().outcome).toBe('any');
    await user.selectOptions(outcome, 'ok');
    expect(value().outcome).toBeUndefined();

    const input = screen.getByLabelText('Input');
    await user.type(input, '{{"folder": ');
    expect(screen.getByText('Not valid JSON yet.')).toBeInTheDocument();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    await user.type(input, '"inbox"}');
    expect(value().input).toEqual({ folder: 'inbox' });
    await user.clear(input);
    expect(value().input).toBeUndefined();
    expect(screen.queryByText('Not valid JSON yet.')).toBeNull();

    const count = screen.getByRole('group', { name: 'Count' });
    expect(within(count).getByLabelText('Exactly')).toHaveValue(2);
    expect(value().operation).toBe('send');
  });

  it('content: phrase lists, regex and case sensitivity', async () => {
    const errors = new Map([['c.contains.0', ['String should have at least 1 character']]]);
    const { user, value } = setup(schemaOf('content'), { contains: [''] }, { errors });
    const contains = screen.getByRole('group', { name: 'Contains' });
    expect(within(contains).getByText('String should have at least 1 character')).toBeInTheDocument();
    await user.type(within(contains).getByLabelText('Contains 1'), 'verify');
    await user.click(within(contains).getByRole('button', { name: 'Add to contains' }));
    await user.type(within(contains).getByLabelText('Contains 2'), 'phone');
    expect(value().contains).toEqual(['verify', 'phone']);
    await user.click(within(contains).getByRole('button', { name: 'Remove contains 1' }));
    expect(value().contains).toEqual(['phone']);
    const not = screen.getByRole('group', { name: 'Not contains' });
    expect(within(not).getByText('None yet.')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Regex'), 'INV-\\d+');
    expect(value().regex).toBe('INV-\\d+');
    await user.click(screen.getByLabelText('Case sensitive'));
    expect(value().case_sensitive).toBe(true);
    await user.click(screen.getByLabelText('Case sensitive'));
    expect(value().case_sensitive).toBeUndefined();
  });

  it('policy: optional nested state can be switched on and off', async () => {
    const { user, value } = setup(schemaOf('policy'), { forbid: { operation: 'send' } });
    const unless = screen.getByRole('group', { name: 'Unless this state held beforehand' });
    await user.click(within(unless).getByRole('checkbox', { name: /Use/ }));
    expect(value().unless).toEqual({});
    await user.selectOptions(within(unless).getByLabelText('Folder'), 'inbox');
    expect(value().unless).toEqual({ where: { folder: 'inbox' } });
    await user.click(within(unless).getByRole('checkbox', { name: /Use/ }));
    expect(value().unless).toBeUndefined();
  });

  it('temporal: events from the scenario and integer windows; safety: invariants with explanations', async () => {
    const { user, value } = setup(schemaOf('temporal'), { first: { event: '' }, then: { action: { operation: 'reply' } } });
    const first = screen.getByRole('group', { name: 'First' });
    await user.type(within(first).getByLabelText('Event'), 'ev-1');
    await user.type(screen.getByLabelText('Within seconds'), '600');
    expect(value()).toMatchObject({ first: { event: 'ev-1' }, within_seconds: 600 });
    await user.clear(screen.getByLabelText('Within seconds'));
    expect(value().within_seconds).toBeUndefined();
  });

  it('safety invariants and a generic schema (map, number, enum default, optional strings, unknown)', async () => {
    const s = setup(schemaOf('safety'), {});
    expect(screen.getByRole('option', { name: /no_external_recipients — The agent never addressed/ })).toBeInTheDocument();
    await s.user.selectOptions(screen.getByLabelText(/Invariant/), 'no_limit_exceeded');
    expect(s.value().invariant).toBe('no_limit_exceeded');
  });

  it('count modes start from whatever is set', async () => {
    const a = setup(schemaOf('state'), {});
    await a.user.selectOptions(screen.getByLabelText('Expected number'), 'range');
    expect(a.value().count).toEqual({ min: 1, max: 1 });
  });

  it('count from an at-most value', async () => {
    const a = setup(schemaOf('state'), { count: { max: 4 } });
    await a.user.selectOptions(screen.getByLabelText('Expected number'), 'eq');
    expect(a.value().count).toEqual({ eq: 4 });
  });

  it('renders a generic schema', async () => {
    const schema: JsonSchema = {
      type: 'object',
      properties: {
        mode: { enum: ['a', 'b'], default: 'a' },
        level: { enum: ['x', 'y'] },
        pick: { enum: ['x', 'y'] },
        ratio: { type: 'number', default: 0.5 },
        note: { type: 'string', default: 'hi' },
        tags: { type: 'object', additionalProperties: true, description: 'Free-form tags.' },
        blob: {},
        flag: { type: 'boolean', default: true },
        items: { type: 'array', items: { type: 'object' } },
      },
      required: ['pick'],
    };
    const { user, value } = setup(schema, { tags: { a: 1 } });
    expect(screen.getByLabelText('Tags')).toHaveValue('{\n  "a": 1\n}');
    expect(screen.getByText('Free-form tags.')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Default (a)' })).toBeInTheDocument();
    expect(screen.getAllByRole('option', { name: 'Not set' }).length).toBeGreaterThan(0);
    expect(screen.getByRole('option', { name: 'Choose…' })).toBeInTheDocument();
    expect(screen.getByLabelText('Note')).toHaveAttribute('placeholder', 'hi');
    expect(screen.getByLabelText('Flag')).toBeChecked();
    await user.click(screen.getByLabelText('Flag'));
    expect(value().flag).toBe(false);
    await user.type(screen.getByLabelText('Ratio'), '2');
    expect(value().ratio).toBe(2);
    await user.type(screen.getByLabelText('Blob'), '[[1]');
    expect(value().blob).toEqual([1]);
    await user.type(screen.getByLabelText('Items'), '[[]');
    expect(value().items).toEqual([]);
  });
});
