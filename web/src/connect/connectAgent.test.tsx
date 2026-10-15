import { act, fireEvent, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setMockListeners } from '../mocks/connect';
import { MOCK_KEYS } from '../mocks/db';
import { mockDb, server } from '../test/server';
import { OPERATOR_SESSION, renderApp, sessionFor } from '../test/utils';

const PAGE = '/runs/run_demo/connect';

async function openConnect(path = PAGE, session = sessionFor('controller')) {
  const r = renderApp(path, session);
  await screen.findByTestId('connection-summary');
  return r;
}

async function pasteKey(user: ReturnType<typeof renderApp>['user'], key: string) {
  await user.type(screen.getByLabelText('Saved agent key'), key);
  await user.click(screen.getByRole('button', { name: 'Use this key' }));
}

describe('Connect your agent', () => {
  it('shows the bound mailbox and asks for an interface', async () => {
    await openConnect();
    expect(screen.getByRole('heading', { level: 1, name: 'Connect your agent' })).toBeInTheDocument();
    expect(screen.getByTestId('mailbox-address')).toHaveTextContent('finance@acme.test');
    expect(screen.getByTestId('no-key')).toHaveTextContent('Agent keys are shown once');
    expect(screen.getByTestId('choose-interface')).toBeInTheDocument();
    const keys = screen.getByTestId('agent-keys');
    expect(keys).toHaveTextContent('key_mk_agent');
    expect(keys).toHaveTextContent('active');
    expect(screen.queryByTestId('run-not-running')).toBeNull();
  });

  it('every interface shows its address, auth and snippets with a placeholder key', async () => {
    const { user, router } = await openConnect();
    const cases: [RegExp, string, string][] = [
      [/Gmail API/, 'http://127.0.0.1:8000/gmail/v1', 'static_discovery=True'],
      [/Microsoft Graph/, 'http://127.0.0.1:8000/graph/v1.0', 'BaseBearerTokenAuthenticationProvider'],
      [/IMAP \/ SMTP/, '127.0.0.1:1143', 'imaplib.IMAP4'],
      [/MCP over HTTP/, 'http://127.0.0.1:8000/mcp', 'claude mcp add --transport http'],
      [/MCP over stdio/, 'uv run python -m mailsim.serve mcp-stdio', 'MAILSIM_API_KEY'],
      [/Neutral REST/, 'http://127.0.0.1:8000/v1', '/mailboxes/mbx_finance/folders'],
    ];
    for (const [name, address, snippet] of cases) {
      await user.click(screen.getByRole('radio', { name }));
      const details = await screen.findByRole('heading', { level: 2, name });
      const section = details.closest('section') as HTMLElement;
      expect(section).toHaveTextContent(address);
      expect(section).toHaveTextContent(snippet);
      expect(section).toHaveTextContent('<AGENT_KEY>');
      expect(section.textContent).not.toContain('msk_');
    }
    expect(router.state.location.search).toBe('?interface=rest');
    await user.click(screen.getByRole('radio', { name: /IMAP \/ SMTP/ }));
    const table = screen.getByTestId('mail-client-settings');
    expect(table).toHaveTextContent('Normal password');
    expect(table).toHaveTextContent('finance@acme.test');
    expect(screen.getByTestId('listener-imap')).toHaveTextContent('running');
    expect(screen.getByText(/Place a copy in Sent/)).toBeInTheDocument();
  });

  it('listener down: shows how to start it, and the test reports it', async () => {
    setMockListeners(mockDb, { imap: false });
    const { user } = await openConnect(`${PAGE}?interface=imap_smtp`);
    const imap = await screen.findByTestId('listener-imap');
    expect(imap).toHaveTextContent('not running');
    expect(imap).toHaveTextContent('uv run python -m mailsim.serve protocols');
    expect(screen.getByTestId('listener-smtp')).toHaveTextContent('running');
    await pasteKey(user, MOCK_KEYS.agent);
    await user.click(screen.getByRole('button', { name: 'Test IMAP connection' }));
    const fail = await screen.findByTestId('test-fail-imap');
    expect(fail).toHaveTextContent('IMAP: failed (LISTENER_DOWN)');
    expect(fail).toHaveTextContent('mailsim.serve protocols');
    await user.click(screen.getByRole('button', { name: 'Test SMTP connection' }));
    expect(await screen.findByTestId('test-ok-smtp')).toHaveTextContent('235');
  });

  it('listener status falls back to the endpoint when /system/status fails', async () => {
    server.use(http.get('*/v1/system/status', () => HttpResponse.json({ error: { code: 'UNSUPPORTED_OPERATION', message: 'x', details: {}, request_id: 'r' } }, { status: 501 })));
    await openConnect(`${PAGE}?interface=imap_smtp`);
    expect(await screen.findByTestId('listener-smtp')).toHaveTextContent('running');
    expect(screen.getByTestId('status-pill')).toHaveTextContent('Server status unavailable');
  });

  it('test connection needs a key, then succeeds for each interface with the right key', async () => {
    const { user } = await openConnect(`${PAGE}?interface=gmail`);
    await user.click(screen.getByRole('button', { name: 'Test Gmail API connection' }));
    expect(screen.getByText(/Testing needs the agent key/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Use this key' }));
    expect(screen.getByText('Paste the agent key first.')).toBeInTheDocument();
    await pasteKey(user, MOCK_KEYS.agent);
    expect(screen.getByTestId('agent-key-value')).toHaveTextContent('••••••••');
    await user.click(screen.getByRole('button', { name: 'Test Gmail API connection' }));
    expect(await screen.findByTestId('test-ok-gmail')).toHaveTextContent('finance@acme.test');
    expect(screen.getByTestId('test-ok-gmail')).toHaveTextContent('Status 200');

    for (const [radio, button, iface] of [
      [/Microsoft Graph/, 'Test Microsoft Graph connection', 'graph'],
      [/MCP over HTTP/, 'Test MCP over HTTP connection', 'mcp_http'],
      [/MCP over stdio/, 'Test MCP over stdio connection', 'mcp_stdio'],
      [/Neutral REST/, 'Test REST connection', 'rest'],
    ] as const) {
      await user.click(screen.getByRole('radio', { name: radio }));
      await user.click(screen.getByRole('button', { name: button }));
      expect(await screen.findByTestId(`test-ok-${iface}`)).toBeInTheDocument();
    }
  });

  it('a wrong key shows the exact error with a hint', async () => {
    const { user } = await openConnect(`${PAGE}?interface=graph`);
    await pasteKey(user, 'msk_000000000000_wrong');
    await user.click(screen.getByRole('button', { name: 'Test Microsoft Graph connection' }));
    const fail = await screen.findByTestId('test-fail-graph');
    expect(fail).toHaveTextContent('Microsoft Graph: failed (InvalidAuthenticationToken, 401)');
    expect(fail).toHaveTextContent('Access token is empty or invalid.');
    expect(fail).toHaveTextContent('Mint a new agent key');
    expect(screen.getByTestId('agent-key-value')).toHaveTextContent('msk_000000000000_••••••••');
  });

  it('failed tests show details; API errors show the envelope', async () => {
    server.use(
      http.post('*/v1/runs/:runId/connection-tests', async ({ request }) => {
        const body = (await request.json()) as { interface: string };
        if (body.interface === 'rest') {
          return HttpResponse.json({ interface: 'rest', ok: false, summary: 'nope', status_code: null, error_code: null, error_message: null, details: { tried: 'GET /v1' } });
        }
        return HttpResponse.json({ error: { code: 'UNSUPPORTED_OPERATION', message: 'not implemented yet', details: {}, request_id: 'req_x' } }, { status: 501 });
      }),
    );
    const { user } = await openConnect(`${PAGE}?interface=rest`);
    await pasteKey(user, MOCK_KEYS.agent);
    await user.click(screen.getByRole('button', { name: 'Test REST connection' }));
    const fail = await screen.findByTestId('test-fail-rest');
    expect(fail).toHaveTextContent('REST: failed');
    expect(within(fail).getByText('Details')).toBeInTheDocument();
    expect(fail).toHaveTextContent('GET /v1');
    await user.click(screen.getByRole('radio', { name: /Gmail API/ }));
    await user.click(screen.getByRole('button', { name: 'Test Gmail API connection' }));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('UNSUPPORTED_OPERATION (HTTP 501)');
  });

  it('reveal fills snippets with the real key; forget clears it', async () => {
    const { user } = await openConnect(`${PAGE}?interface=mcp_http`);
    await pasteKey(user, MOCK_KEYS.agent);
    const snippet = screen.getByTestId('snippet-mcp-http-claude');
    expect(snippet).toHaveTextContent('<AGENT_KEY>');
    await user.click(screen.getByRole('button', { name: 'Reveal key' }));
    expect(screen.getByRole('button', { name: 'Hide key' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('agent-key-value')).toHaveTextContent(MOCK_KEYS.agent);
    expect(screen.getByTestId('snippet-mcp-http-claude')).toHaveTextContent(`Bearer ${MOCK_KEYS.agent}`);
    await user.click(within(screen.getByTestId('snippet-mcp-http-claude')).getByRole('button', { name: 'Copy Claude Code' }));
    expect(await screen.findByText('Copied to clipboard.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Hide key' }));
    expect(screen.getByTestId('snippet-mcp-http-claude')).toHaveTextContent('<AGENT_KEY>');
    await user.click(screen.getByRole('button', { name: 'Forget key' }));
    expect(screen.getByTestId('no-key')).toBeInTheDocument();
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(MOCK_KEYS.agent);
  });

  it('mint: confirmation, key shown once and revealed, earlier key revoked', async () => {
    const { user } = await openConnect();
    const mintButton = screen.getByRole('button', { name: 'Mint a new agent key' });
    await user.click(mintButton);
    const dialog = screen.getByRole('alertdialog', { name: 'Mint a new agent key?' });
    expect(dialog).toHaveTextContent('revokes this run’s other agent keys (key_mk_agent)');
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(mintButton).toHaveFocus();

    await user.click(mintButton);
    await user.click(screen.getByRole('button', { name: 'Mint key' }));
    expect(await screen.findByText(/New agent key minted/)).toBeInTheDocument();
    expect(screen.getByTestId('agent-key-value')).toHaveTextContent('msk_000000000001_mockMintedAgentKeyForTests0001abcdef');
    const keys = screen.getByTestId('agent-keys');
    await within(keys).findByRole('rowheader', { name: 'key_minted_0001' });
    const oldRow = within(keys).getByRole('rowheader', { name: 'key_mk_agent' }).closest('tr') as HTMLElement;
    expect(oldRow).toHaveTextContent('revoked');
    expect(mockDb.keys[MOCK_KEYS.agent]).toBeUndefined();
  });

  it('mint fails for a finished run', async () => {
    (mockDb.runs.run_demo as { run: { status: string } }).run.status = 'CANCELLED';
    const { user } = await openConnect();
    expect(screen.getByTestId('run-not-running')).toHaveTextContent('This run is CANCELLED');
    await user.click(screen.getByRole('button', { name: 'Mint a new agent key' }));
    await user.click(screen.getByRole('button', { name: 'Mint key' }));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('CONFLICT (HTTP 409)');
  });

  it('revoke one key after confirmation; the held key is forgotten when none is left', async () => {
    const { user } = await openConnect();
    await pasteKey(user, MOCK_KEYS.agent);
    await user.click(screen.getByRole('button', { name: 'Revoke key_mk_agent' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Revoke key_mk_agent?' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await user.click(screen.getByRole('button', { name: 'Revoke key_mk_agent' }));
    await user.click(screen.getByRole('button', { name: 'Revoke' }));
    expect(await screen.findByText('revoked')).toBeInTheDocument();
    expect(screen.getByTestId('no-key')).toBeInTheDocument();
    expect(mockDb.keys[MOCK_KEYS.agent]).toBeUndefined();
  });

  it('revoke all agent keys, and revocation errors', async () => {
    mockDb.keys.mk_agent_second = { role: 'agent', project: 'prj_main', runId: 'run_demo', mailboxId: 'mbx_finance' };
    const { user } = await openConnect();
    await user.click(screen.getByRole('button', { name: 'Revoke all agent keys of this run' }));
    expect(screen.getByRole('alertdialog', { name: 'Revoke 2 agent keys?' })).toHaveTextContent('these keys');
    server.use(http.delete('*/v1/keys/:keyId', () => HttpResponse.json({ error: { code: 'NOT_FOUND', message: 'gone', details: {}, request_id: 'r' } }, { status: 404 })));
    await user.click(screen.getByRole('button', { name: 'Revoke' }));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('NOT_FOUND (HTTP 404)');
    server.resetHandlers();
  });

  it('a READY run and a run without keys', async () => {
    await openConnect('/runs/run_empty/connect');
    expect(screen.getByTestId('run-not-running')).toHaveTextContent('This run is READY');
    expect(screen.getByText('No agent keys have been issued for this run.')).toBeInTheDocument();
  });

  it('unknown runs and non-controllers', async () => {
    renderApp('/runs/run_nope/connect', sessionFor('controller'));
    expect(await screen.findByTestId('api-error')).toHaveTextContent('NOT_FOUND (HTTP 404)');
  });

  it('evaluators see a role gate', async () => {
    renderApp(PAGE, sessionFor('evaluator'));
    expect(await screen.findByTestId('role-gate')).toHaveTextContent('Connecting an agent requires a controller key');
  });

  it('works for the local no-auth operator', async () => {
    mockDb.localNoAuth = true;
    renderApp(`${PAGE}?interface=gmail`, OPERATOR_SESSION);
    expect(await screen.findByTestId('details-gmail')).toBeInTheDocument();
    expect(await screen.findByTestId('status-pill')).toHaveTextContent('Local no-auth');
  });

  it('snippet regions are keyboard focusable', async () => {
    await openConnect(`${PAGE}?interface=rest`);
    const region = screen.getByRole('region', { name: 'curl' });
    expect(region).toHaveAttribute('tabindex', '0');
    act(() => region.focus());
    expect(region).toHaveFocus();
    fireEvent.keyDown(region, { key: 'Escape' }); // no dialog open: nothing happens
  });
});

describe('Connect your agent entry points', () => {
  it('start run hands the fresh key to Connect (memory only)', async () => {
    const { user, router } = renderApp('/scenarios/finance.invoice-bank-change/start', sessionFor('controller'));
    await user.click(await screen.findByRole('button', { name: 'Create run' }));
    const panel = await screen.findByTestId('agent-credentials');
    const next = within(panel).getByRole('link', { name: 'Next: connect your agent' });
    expect(next).toHaveAttribute('href', '/runs/run_001/connect');
    await user.click(next);
    expect(router.state.location.pathname).toBe('/runs/run_001/connect');
    expect(router.state.location.state).toBeNull();
    expect(await screen.findByTestId('agent-key-value')).toHaveTextContent('••••••••');
    await user.click(screen.getByRole('button', { name: 'Reveal key' }));
    expect(screen.getByTestId('agent-key-value')).toHaveTextContent('mk_agent_run_001_finance');
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('mk_agent_run_001_finance');
  });

  it('run detail links to Connect for controllers only', async () => {
    renderApp('/runs/run_demo', sessionFor('controller'));
    expect(await screen.findByTestId('connect-agent-link')).toHaveAttribute('href', '/runs/run_demo/connect');
  });

  it('run detail hides Connect from evaluators', async () => {
    renderApp('/runs/run_demo', sessionFor('evaluator'));
    await screen.findByTestId('run-summary');
    expect(screen.queryByTestId('connect-agent-link')).toBeNull();
  });
});
