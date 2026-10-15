import type { InterfaceEndpoint, ListenerStatus, RunConnection, SystemStatus } from '../api/types';
import { CopyButton } from '../components/CopyButton';
import { SnippetBlock } from './SnippetBlock';
import { TestConnection } from './TestConnection';
import {
  DEFAULT_STDIO_COMMAND,
  endpointFor,
  INTERFACE_LABEL,
  NOTES,
  shellJoin,
  snippetsFor,
  START_PROTOCOLS,
  type Choice,
} from './snippets';

const security = (tls: InterfaceEndpoint['tls']) =>
  tls === 'starttls' ? 'STARTTLS (accept the self-signed certificate)' : 'None (plain text, loopback only)';

function UrlValue({ label, url }: { label: string; url: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className="row tight">
        <code data-testid="endpoint-url">{url}</code>
        <CopyButton value={url} label={`Copy ${label.toLowerCase()}`} />
      </dd>
    </>
  );
}

/** Up/down of the IMAP or SMTP listener, with how to start it when it is down. */
export function ListenerLine({ name, ep, status }: { name: 'IMAP' | 'SMTP'; ep: InterfaceEndpoint; status?: ListenerStatus }) {
  const running = status ? status.running : ep.available;
  const host = status?.host ?? ep.host;
  const port = status?.port ?? ep.port;
  return (
    <li data-testid={`listener-${name.toLowerCase()}`}>
      <strong>{name} listener:</strong>{' '}
      {running ? (
        <span className="badge badge-running">running</span>
      ) : (
        <span className="badge badge-error">not running</span>
      )}{' '}
      {host && port ? (
        <code>
          {host}:{port}
        </code>
      ) : null}
      {!running && (
        <>
          {' '}
          Start the listeners next to the API server (same <code>MAILSIM_DATABASE_URL</code> and{' '}
          <code>MAILSIM_BLOB_DIR</code>): <code>{START_PROTOCOLS}</code> (add <code>--tls-self-signed</code> for STARTTLS).
        </>
      )}
    </li>
  );
}

function MailClientSettings({ imap, smtp, address }: { imap: InterfaceEndpoint; smtp: InterfaceEndpoint; address: string }) {
  const rows: [string, InterfaceEndpoint][] = [
    ['Incoming (IMAP)', imap],
    ['Outgoing (SMTP)', smtp],
  ];
  return (
    <table data-testid="mail-client-settings">
      <caption>Mail client settings (Thunderbird: Configure manually; Outlook: IMAP account, advanced setup)</caption>
      <thead>
        <tr>
          <th scope="col">Server</th>
          <th scope="col">Host</th>
          <th scope="col">Port</th>
          <th scope="col">Connection security</th>
          <th scope="col">Authentication</th>
          <th scope="col">Username</th>
          <th scope="col">Password</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, ep]) => (
          <tr key={label}>
            <th scope="row">{label}</th>
            <td>
              <code>{ep.host ?? '127.0.0.1'}</code>
            </td>
            <td>
              <code>{ep.port ?? '—'}</code>
            </td>
            <td>{security(ep.tls)}</td>
            <td>Normal password</td>
            <td>
              <code>{ep.username ?? address}</code>
            </td>
            <td>the agent key</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Everything needed to connect through one interface choice. */
export function InterfaceDetails({
  choice,
  conn,
  status,
  shownKey,
}: {
  choice: Choice;
  conn: RunConnection;
  status?: SystemStatus;
  /** The key to put into snippets (only when revealed), else null = placeholder. */
  shownKey: string | null;
}) {
  const ep = (i: Choice['interfaces'][number]) => endpointFor(conn, i) ?? { interface: i, available: false };
  const first = ep(choice.interfaces[0] as Choice["interfaces"][number]);
  const isMail = choice.id === 'imap_smtp';
  const unavailable = !isMail && !first.available;
  const snippets = snippetsFor(choice.id, conn, shownKey);
  return (
    <section aria-labelledby="iface-h" className="card" data-testid={`details-${choice.id}`}>
      <h2 id="iface-h">
        {choice.title} <span className="muted">({choice.alias})</span>
      </h2>
      {unavailable && (
        <p role="note" className="notice" data-testid="iface-unavailable">
          This server does not serve {choice.title} right now.{first.note ? ` ${first.note}` : ''}
        </p>
      )}
      <dl className="kv">
        <dt>Mailbox</dt>
        <dd>
          <code>{conn.mailbox_address}</code>
        </dd>
        {first.base_url && <UrlValue label="Base URL" url={first.base_url} />}
        {isMail &&
          (['imap', 'smtp'] as const).map((i) => (
            <UrlValue key={i} label={`${INTERFACE_LABEL[i]} server`} url={`${ep(i).host ?? '127.0.0.1'}:${ep(i).port ?? ''}`} />
          ))}
        {isMail && (
          <>
            <dt>Security</dt>
            <dd>{security(ep('imap').tls)}</dd>
            <dt>Username</dt>
            <dd>
              <code>{ep('imap').username ?? conn.mailbox_address}</code>
            </dd>
            <dt>Password</dt>
            <dd>the agent key</dd>
          </>
        )}
        {choice.id === 'mcp_stdio' && (
          <>
            <dt>Command</dt>
            <dd>
              <code>{shellJoin(first.command ?? DEFAULT_STDIO_COMMAND)}</code>
            </dd>
          </>
        )}
        <dt>Authentication</dt>
        <dd>{choice.auth}</dd>
      </dl>
      {first.note && !unavailable && <p className="hint">{first.note}</p>}
      {isMail && (
        <>
          <ul className="plain" aria-label="Listener status">
            <ListenerLine name="IMAP" ep={ep('imap')} status={status?.listeners.imap} />
            <ListenerLine name="SMTP" ep={ep('smtp')} status={status?.listeners.smtp} />
          </ul>
          <MailClientSettings imap={ep('imap')} smtp={ep('smtp')} address={conn.mailbox_address} />
        </>
      )}
      <h3>Copy-paste examples</h3>
      {!shownKey && (
        <p className="hint">
          The examples use the placeholder <code>&lt;AGENT_KEY&gt;</code>. Reveal the key above to fill in the real value.
        </p>
      )}
      {snippets.map((s) => (
        <SnippetBlock key={s.id} snippet={s} />
      ))}
      <h3>Good to know</h3>
      <ul>
        {NOTES[choice.id].map((n) => (
          <li key={n}>{n}</li>
        ))}
        <li>
          Full profile: <code>{choice.doc}</code> in the mailsim repository.
        </li>
      </ul>
      <h3>Test the connection</h3>
      <p className="hint">The API server makes a real call through this interface with the agent key and reports the result.</p>
      <div className="row tests">
        {choice.interfaces.map((i) => (
          <TestConnection key={i} runId={conn.run_id} iface={i} />
        ))}
      </div>
    </section>
  );
}
