import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { goSection, openNew, SLOW } from '../test/builder';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

afterEach(() => server.events.removeAllListeners());
vi.setConfig({ testTimeout: 30_000 });

const yamlBox = () => screen.getByLabelText('Scenario YAML') as HTMLTextAreaElement;

async function showYaml(user: ReturnType<typeof renderApp>['user']) {
  await user.click(screen.getByRole('button', { name: 'Show YAML' }));
  await waitFor(() => expect(yamlBox().value).toContain('schema_version'), SLOW);
}

describe('YAML view', () => {
  it('renders the form, applies edits, rejects parse errors and discards edits', async () => {
    const { user } = await openNew(true);
    await showYaml(user);
    const original = yamlBox().value;
    expect(original).toContain('title: Invoice review with a bank-detail change request');
    expect(screen.getByRole('button', { name: 'Apply YAML to form' })).toBeDisabled();

    await user.clear(yamlBox());
    await user.click(yamlBox());
    await user.paste('scenario:\n  - [');
    expect(screen.getByText('Unapplied YAML edits')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Apply YAML to form' }));
    expect(await screen.findByTestId('yaml-error')).toHaveTextContent(/YAML not applied — line \d+:/);
    expect(yamlBox()).toHaveAttribute('aria-invalid', 'true');
    await user.click(screen.getByRole('button', { name: 'Discard YAML edits' }));
    expect(screen.queryByTestId('yaml-error')).toBeNull();
    expect(yamlBox().value).toBe(original);

    // a scalar document is not a scenario
    await user.clear(yamlBox());
    await user.type(yamlBox(), 'just text');
    await user.click(screen.getByRole('button', { name: 'Apply YAML to form' }));
    expect(await screen.findByTestId('yaml-error')).toHaveTextContent('<root>: the document must be a mapping');

    await user.clear(yamlBox());
    await user.click(yamlBox());
    await user.paste(original.replace('title: Invoice review with a bank-detail change request', 'title: From YAML'));
    await user.click(screen.getByRole('button', { name: 'Apply YAML to form' }));
    expect(await screen.findByTestId('builder-notice')).toHaveTextContent('YAML applied to the form.');
    expect(screen.getByLabelText(/^Title/)).toHaveValue('From YAML');
    await waitFor(() => expect(yamlBox().value).toBe(original.replace('Invoice review with a bank-detail change request', 'From YAML')), SLOW);

    await user.click(screen.getByRole('button', { name: 'Hide YAML' }));
    expect(screen.queryByTestId('yaml-panel')).toBeNull();
  });

  it('handles a parse response without a document or error, and request failures', async () => {
    const { user } = await openNew(true);
    await showYaml(user);
    server.use(http.post('*/v1/tools/yaml-to-json', () => HttpResponse.json({ document: null, error: null })));
    await user.type(yamlBox(), '#');
    await user.click(screen.getByRole('button', { name: 'Apply YAML to form' }));
    expect(await screen.findByTestId('yaml-error')).toHaveTextContent('<root>: The text is not a scenario document.');
    server.use(http.post('*/v1/tools/yaml-to-json', () => HttpResponse.json({ error: { code: 'INTERNAL', message: 'parser down', details: {}, request_id: 'r' } }, { status: 500 })));
    await user.click(screen.getByRole('button', { name: 'Apply YAML to form' }));
    expect(await screen.findByText(/parser down/)).toBeInTheDocument();
  });

  it('shows render failures', async () => {
    server.use(http.post('*/v1/tools/json-to-yaml', () => HttpResponse.json({ error: { code: 'INTERNAL', message: 'render down', details: {}, request_id: 'r' } }, { status: 500 })));
    const { user } = await openNew(true);
    await user.click(screen.getByRole('button', { name: 'Show YAML' }));
    expect(await screen.findByText(/render down/)).toBeInTheDocument();
  });

  it('keeps the id while editing and drops inline assertions', async () => {
    const { user } = renderApp('/scenarios/finance.invoice-bank-change/edit', sessionFor('controller'));
    await screen.findByLabelText(/Scenario id/);
    await showYaml(user);
    const text = yamlBox().value.replace('id: finance.invoice-bank-change', 'id: other.key').replace(
      'evaluation:\n',
      'evaluation:\n    assertions:\n      - id: secret\n        type: semantic\n',
    );
    await user.clear(yamlBox());
    await user.click(yamlBox());
    await user.paste(text);
    await user.click(screen.getByRole('button', { name: 'Apply YAML to form' }));
    const notice = await screen.findByTestId('builder-notice');
    expect(notice).toHaveTextContent('The scenario id cannot change while editing');
    expect(notice).toHaveTextContent('1 inline evaluation assertion(s) were removed');
    const basics = await goSection(user, 'Basics');
    expect(basics.getByLabelText(/Scenario id/)).toHaveValue('finance.invoice-bank-change');
  });

  it('reports unknown keys and problems in pasted YAML, and keeps the version above published ones', async () => {
    const { user } = renderApp('/scenarios/finance.invoice-bank-change/edit', sessionFor('controller'));
    await screen.findByLabelText(/Scenario id/);
    await showYaml(user);
    const extra = Array.from({ length: 9 }, (_, i) => `x${i}: 1`).join('\n');
    const text = yamlBox().value.replace('version: 4', 'version: 2').replace('title:', 'titel: typo\n  title:') + extra + '\n';
    await user.clear(yamlBox());
    await user.click(yamlBox());
    await user.paste(text);
    await user.click(screen.getByRole('button', { name: 'Apply YAML to form' }));
    const notice = await screen.findByTestId('builder-notice');
    expect(notice).toHaveTextContent('Not part of the scenario format, so dropped: scenario.titel, x0, x1, x2, x3, x4, x5, x6 and 2 more.');
    expect(notice).toHaveTextContent('The version was set to 4');
    expect(notice).toHaveTextContent(/The YAML as pasted had \d+ problem\(s\)/);
    const basics = await goSection(user, 'Basics');
    expect(basics.getByLabelText(/Version/)).toHaveValue(4);
  });

  it('applies YAML even when pre-validation fails', async () => {
    const { user } = await openNew(true);
    await showYaml(user);
    server.use(http.post('*/v1/scenario-drafts/validate', () => HttpResponse.json({ error: { code: 'INTERNAL', message: 'x', details: {}, request_id: 'r' } }, { status: 500 })));
    await user.type(yamlBox(), '\n');
    await user.click(screen.getByRole('button', { name: 'Apply YAML to form' }));
    expect(await screen.findByTestId('builder-notice')).toHaveTextContent(/^YAML applied to the form\. Dismiss$/);
  });
});
