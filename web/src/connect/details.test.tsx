import { fireEvent, render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { RunConnection } from '../api/types';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { server } from '../test/server';
import { renderApp, sessionFor } from '../test/utils';

describe('ConfirmDialog', () => {
  it('ignores other keys, cancels on Escape and shows a busy state', () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    const { rerender } = render(
      <ConfirmDialog title="Sure?" confirmLabel="Do it" onConfirm={onConfirm} onCancel={onCancel}>
        <p>Body</p>
      </ConfirmDialog>,
    );
    const dialog = screen.getByRole('alertdialog', { name: 'Sure?' });
    expect(dialog).toHaveAccessibleDescription('Body');
    fireEvent.keyDown(dialog, { key: 'Enter' });
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Do it' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    rerender(
      <ConfirmDialog title="Sure?" confirmLabel="Do it" busy onConfirm={onConfirm} onCancel={onCancel}>
        <p>Body</p>
      </ConfirmDialog>,
    );
    expect(screen.getByRole('button', { name: 'Working…' })).toBeDisabled();
  });
});

function connection(over: Partial<RunConnection>): RunConnection {
  return {
    run_id: 'run_demo',
    run_status: 'RUNNING',
    mailbox_id: 'mbx_finance',
    mailbox_address: 'finance@acme.test',
    persona_id: 'finance',
    agent_keys: [{ key_id: 'key_1', created_at: 'T0', expires_at: 'T9', revoked: false }],
    interfaces: [],
    ...over,
  };
}
const serve = (c: RunConnection) => server.use(http.get('*/v1/runs/:runId/connection', () => HttpResponse.json(c)));

describe('interface details edge cases', () => {
  it('interfaces the server does not offer, with its note', async () => {
    serve(
      connection({
        interfaces: [{ interface: 'graph', available: false, note: 'Graph adapter disabled by configuration.' }],
      }),
    );
    renderApp('/runs/run_demo/connect?interface=graph', sessionFor('controller'));
    expect(await screen.findByTestId('iface-unavailable')).toHaveTextContent('does not serve Microsoft Graph right now. Graph adapter disabled');
    expect(screen.getByTestId('agent-keys')).toHaveTextContent('T9');
  });

  it('an unavailable interface without a note, and one missing entirely', async () => {
    serve(connection({ interfaces: [{ interface: 'gmail', available: false }] }));
    const { user } = renderApp('/runs/run_demo/connect?interface=gmail', sessionFor('controller'));
    expect(await screen.findByTestId('iface-unavailable')).toHaveTextContent('does not serve Gmail API right now.');
    await user.click(screen.getByRole('radio', { name: /Neutral REST/ }));
    expect(screen.getByTestId('iface-unavailable')).toHaveTextContent('does not serve Neutral REST');
  });

  it('STARTTLS listeners and missing host/port/username fall back to defaults', async () => {
    serve(
      connection({
        interfaces: [
          { interface: 'imap', available: true, tls: 'starttls', note: 'Self-signed certificate.' },
          { interface: 'smtp', available: false, tls: 'starttls' },
        ],
      }),
    );
    server.use(http.get('*/v1/system/status', () => HttpResponse.json({ error: { code: 'X', message: 'x', details: {}, request_id: 'r' } }, { status: 500 })));
    renderApp('/runs/run_demo/connect?interface=imap_smtp', sessionFor('controller'));
    const table = await screen.findByTestId('mail-client-settings');
    expect(table).toHaveTextContent('STARTTLS (accept the self-signed certificate)');
    expect(table).toHaveTextContent('—');
    expect(table).toHaveTextContent('finance@acme.test');
    expect(screen.getByText('Self-signed certificate.')).toBeInTheDocument();
    expect(screen.getByTestId('listener-imap')).toHaveTextContent('running');
    expect(screen.getByTestId('listener-imap').querySelector('code')).toBeNull(); // no host:port known
    expect(screen.getByTestId('listener-smtp')).toHaveTextContent('not running');
    expect(screen.getAllByTestId('endpoint-url')[0]).toHaveTextContent('127.0.0.1:');
  });

  it('stdio without a command uses the documented default', async () => {
    serve(connection({ interfaces: [{ interface: 'mcp_stdio', available: true }] }));
    renderApp('/runs/run_demo/connect?interface=mcp_stdio', sessionFor('controller'));
    expect(await screen.findByTestId('details-mcp_stdio')).toHaveTextContent('python -m mailsim.serve mcp-stdio');
  });
});
