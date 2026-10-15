// Interface catalogue and copy-paste snippets for "Connect your agent". The snippet details
// follow docs/compat/{gmail,graph,imap-smtp,mcp}.md; keep them in sync with those documents.
import type { AgentInterface, InterfaceEndpoint, RunConnection } from '../api/types';

export type ChoiceId = 'gmail' | 'graph' | 'imap_smtp' | 'mcp_http' | 'mcp_stdio' | 'rest';

export interface Choice {
  id: ChoiceId;
  title: string;
  /** What people call it ("GSuite simulator"). */
  alias: string;
  /** One line: use this when ... */
  useWhen: string;
  /** How the agent key is presented on this interface. */
  auth: string;
  /** Interfaces behind this choice (one "Test connection" button each). */
  interfaces: AgentInterface[];
  /** docs/compat page for the full profile. */
  doc: string;
}

export const CHOICES: readonly Choice[] = [
  {
    id: 'gmail',
    title: 'Gmail API',
    alias: 'GSuite simulator',
    useWhen: 'your agent already talks to Gmail through google-api-python-client or the Gmail REST API.',
    auth: 'Send the agent key as the OAuth access token: Authorization: Bearer <agent key>. There is no Google sign-in; the token cannot be refreshed.',
    interfaces: ['gmail'],
    doc: 'docs/compat/gmail.md',
  },
  {
    id: 'graph',
    title: 'Microsoft Graph',
    alias: 'Office 365 simulator',
    useWhen: 'your agent uses Outlook / Exchange Online mail through msgraph-sdk or Graph REST calls.',
    auth: 'Send the agent key as the bearer access token (no Entra ID flow): Authorization: Bearer <agent key>.',
    interfaces: ['graph'],
    doc: 'docs/compat/graph.md',
  },
  {
    id: 'imap_smtp',
    title: 'IMAP / SMTP',
    alias: 'Any mail client',
    useWhen: 'your agent (or a desktop client such as Thunderbird or Outlook) reads with IMAP and sends with SMTP.',
    auth: 'Log in with the mailbox address as the username and the agent key as the password (IMAP LOGIN / SMTP AUTH PLAIN).',
    interfaces: ['imap', 'smtp'],
    doc: 'docs/compat/imap-smtp.md',
  },
  {
    id: 'mcp_http',
    title: 'MCP over HTTP',
    alias: 'Claude Code and other MCP clients',
    useWhen: 'your agent is an LLM application that uses MCP tools and can reach a URL.',
    auth: 'Every request carries Authorization: Bearer <agent key>. The server is stateless (no MCP sessions).',
    interfaces: ['mcp_http'],
    doc: 'docs/compat/mcp.md',
  },
  {
    id: 'mcp_stdio',
    title: 'MCP over stdio',
    alias: 'Local MCP process',
    useWhen: 'your MCP client starts tool servers as local processes (Claude Desktop, stdio-only clients).',
    auth: 'The process reads the agent key from the MAILSIM_API_KEY environment variable (never from argv).',
    interfaces: ['mcp_stdio'],
    doc: 'docs/compat/mcp.md',
  },
  {
    id: 'rest',
    title: 'Neutral REST',
    alias: 'mailsim /v1 API',
    useWhen: 'you are writing a new agent or test harness and want the simplest provider-neutral HTTP API.',
    auth: 'Send the agent key as a bearer token: Authorization: Bearer <agent key>.',
    interfaces: ['rest'],
    doc: 'openapi/openapi.json',
  },
];

export const INTERFACE_LABEL: Record<AgentInterface, string> = {
  gmail: 'Gmail API',
  graph: 'Microsoft Graph',
  imap: 'IMAP',
  smtp: 'SMTP',
  mcp_http: 'MCP over HTTP',
  mcp_stdio: 'MCP over stdio',
  rest: 'REST',
};

export const KEY_PLACEHOLDER = '<AGENT_KEY>';
export const DEFAULT_STDIO_COMMAND = ['python', '-m', 'mailsim.serve', 'mcp-stdio'];
export const START_PROTOCOLS = 'uv run python -m mailsim.serve protocols';

export interface Snippet {
  id: string;
  title: string;
  language: 'python' | 'shell' | 'json';
  code: string;
}

export function choiceById(id: string | null): Choice | undefined {
  return CHOICES.find((c) => c.id === id);
}

export function endpointFor(conn: RunConnection, iface: AgentInterface): InterfaceEndpoint | undefined {
  return conn.interfaces.find((e) => e.interface === iface);
}

/** Root URL for google-api-python-client's api_endpoint: the base URL without /gmail/v1. */
export function gmailRoot(baseUrl: string): string {
  return `${baseUrl.replace(/\/+$/, '').replace(/\/gmail\/v1$/, '')}/`;
}

/** host:port of a URL (what kiota's AllowedHostsValidator compares). */
export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

const py = (s: string) => JSON.stringify(s);
const sh = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;
/** argv as a shell command line (quotes only where needed). */
export function shellJoin(argv: readonly string[]): string {
  return argv.map((a) => (/^[A-Za-z0-9_./:=@%+-]+$/.test(a) ? a : sh(a))).join(' ');
}

function gmailSnippets(base: string, address: string, key: string): Snippet[] {
  const root = gmailRoot(base);
  return [
    {
      id: 'gmail-python',
      title: 'Python (google-api-python-client)',
      language: 'python',
      code: `# pip install google-api-python-client google-auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

AGENT_KEY = ${py(key)}  # agent key for ${address}

gmail = build(
    "gmail",
    "v1",
    credentials=Credentials(token=AGENT_KEY),
    client_options={"api_endpoint": ${py(root)}},
    static_discovery=True,
)
print(gmail.users().getProfile(userId="me").execute())
print(gmail.users().messages().list(userId="me", q="is:unread").execute())`,
    },
    {
      id: 'gmail-curl',
      title: 'curl',
      language: 'shell',
      code: `curl -H ${sh(`Authorization: Bearer ${key}`)} \\
  ${sh(`${base.replace(/\/+$/, '')}/users/me/messages?q=is:unread`)}`,
    },
  ];
}

function graphSnippets(base: string, key: string): Snippet[] {
  const b = base.replace(/\/+$/, '');
  return [
    {
      id: 'graph-python',
      title: 'Python (msgraph-sdk)',
      language: 'python',
      code: `# pip install msgraph-sdk
# The Azure Identity provider requires https, so use kiota's bearer provider with a static token.
import asyncio

import httpx
from kiota_abstractions.authentication import (
    AccessTokenProvider,
    AllowedHostsValidator,
    BaseBearerTokenAuthenticationProvider,
)
from msgraph import GraphRequestAdapter, GraphServiceClient
from msgraph.graph_request_adapter import options
from msgraph_core import GraphClientFactory

BASE = ${py(b)}
AGENT_KEY = ${py(key)}


class SandboxKey(AccessTokenProvider):
    def __init__(self, key):
        self.key = key

    async def get_authorization_token(self, uri, additional_authentication_context=None):
        return self.key

    def get_allowed_hosts_validator(self):
        return AllowedHostsValidator([${py(hostOf(b))}])


async def main():
    http = GraphClientFactory.create_with_default_middleware(
        client=httpx.AsyncClient(base_url=BASE), options=options
    )
    adapter = GraphRequestAdapter(BaseBearerTokenAuthenticationProvider(SandboxKey(AGENT_KEY)), http)
    adapter.base_url = BASE
    client = GraphServiceClient(request_adapter=adapter)
    messages = await client.me.mail_folders.by_mail_folder_id("inbox").messages.get()
    for m in messages.value:
        print(m.subject)


asyncio.run(main())`,
    },
    {
      id: 'graph-curl',
      title: 'curl',
      language: 'shell',
      code: `curl -H ${sh(`Authorization: Bearer ${key}`)} \\
  ${sh(`${b}/me/mailFolders/inbox/messages?$top=5&$select=subject,from`)}`,
    },
  ];
}

function imapSmtpSnippets(imap: InterfaceEndpoint, smtp: InterfaceEndpoint, address: string, key: string): Snippet[] {
  const tlsLine = (ep: InterfaceEndpoint, obj: string) =>
    ep.tls === 'starttls' ? `${obj}.starttls()  # the listener offers STARTTLS\n` : '';
  return [
    {
      id: 'imap-smtp-python',
      title: 'Python (imaplib + smtplib)',
      language: 'python',
      code: `import imaplib
import smtplib
from email.message import EmailMessage

ADDRESS = ${py(address)}  # username = the mailbox address
AGENT_KEY = ${py(key)}  # password = the agent key

imap = imaplib.IMAP4(${py(imap.host ?? '127.0.0.1')}, ${imap.port ?? 1143})
${tlsLine(imap, 'imap')}imap.login(ADDRESS, AGENT_KEY)
imap.select("INBOX")
typ, data = imap.uid("FETCH", "1:*", "(FLAGS ENVELOPE)")
print(typ, data)
imap.logout()

msg = EmailMessage()
msg["From"] = ADDRESS
msg["To"] = ADDRESS  # simulated addresses only; others follow the scenario's delivery rule
msg["Subject"] = "Hello from my agent"
msg.set_content("Sent through the mailsim SMTP listener.")

with smtplib.SMTP(${py(smtp.host ?? '127.0.0.1')}, ${smtp.port ?? 1587}) as s:
${tlsLine(smtp, '    s')}    s.login(ADDRESS, AGENT_KEY)
    s.send_message(msg)`,
    },
  ];
}

function mcpHttpSnippets(url: string, key: string): Snippet[] {
  const config = { mcpServers: { mailsim: { type: 'http', url, headers: { Authorization: `Bearer ${key}` } } } };
  return [
    {
      id: 'mcp-http-claude',
      title: 'Claude Code',
      language: 'shell',
      code: `claude mcp add --transport http mailsim ${sh(url)} \\
  --header ${sh(`Authorization: Bearer ${key}`)}`,
    },
    { id: 'mcp-http-json', title: 'MCP client config (.mcp.json)', language: 'json', code: JSON.stringify(config, null, 2) },
  ];
}

function mcpStdioSnippets(command: readonly string[], key: string): Snippet[] {
  const env = { MAILSIM_API_KEY: key, MAILSIM_DATABASE_URL: '<same as the API server>', MAILSIM_BLOB_DIR: '<same as the API server>' };
  const config = { mcpServers: { mailsim: { command: command[0], args: command.slice(1), env } } };
  return [
    {
      id: 'mcp-stdio-claude',
      title: 'Claude Code',
      language: 'shell',
      code: `claude mcp add mailsim --env MAILSIM_API_KEY=${sh(key)} \\
  --env MAILSIM_DATABASE_URL="$MAILSIM_DATABASE_URL" \\
  --env MAILSIM_BLOB_DIR="$MAILSIM_BLOB_DIR" \\
  -- ${shellJoin(command)}`,
    },
    { id: 'mcp-stdio-json', title: 'MCP client config (Claude Desktop, .mcp.json)', language: 'json', code: JSON.stringify(config, null, 2) },
    {
      id: 'mcp-stdio-shell',
      title: 'Run it by hand',
      language: 'shell',
      code: `MAILSIM_API_KEY=${sh(key)} ${shellJoin(command)}`,
    },
  ];
}

function restSnippets(base: string, mailboxId: string, address: string, key: string): Snippet[] {
  const mbx = `${base.replace(/\/+$/, '')}/mailboxes/${encodeURIComponent(mailboxId)}`;
  const auth = sh(`Authorization: Bearer ${key}`);
  const body = JSON.stringify({ to: [address], subject: 'Hello from my agent', text: 'Sent through the REST API.' });
  return [
    {
      id: 'rest-curl',
      title: 'curl',
      language: 'shell',
      code: `# folders and inbox
curl -H ${auth} ${sh(`${mbx}/folders`)}
curl -H ${auth} ${sh(`${mbx}/messages?folder=inbox`)}

# send (simulated recipients only)
curl -X POST -H ${auth} -H 'Content-Type: application/json' \\
  -d ${sh(body)} ${sh(`${mbx}/messages`)}`,
    },
  ];
}

/** Snippets for one choice, with real values filled in. `key` null = placeholder. */
export function snippetsFor(choice: ChoiceId, conn: RunConnection, key: string | null): Snippet[] {
  const k = key ?? KEY_PLACEHOLDER;
  const ep = (i: AgentInterface) => endpointFor(conn, i) ?? { interface: i, available: false };
  const url = (i: AgentInterface) => ep(i).base_url ?? '';
  switch (choice) {
    case 'gmail':
      return gmailSnippets(url('gmail'), conn.mailbox_address, k);
    case 'graph':
      return graphSnippets(url('graph'), k);
    case 'imap_smtp':
      return imapSmtpSnippets(ep('imap'), ep('smtp'), conn.mailbox_address, k);
    case 'mcp_http':
      return mcpHttpSnippets(url('mcp_http'), k);
    case 'mcp_stdio':
      return mcpStdioSnippets(ep('mcp_stdio').command ?? DEFAULT_STDIO_COMMAND, k);
    case 'rest':
      return restSnippets(url('rest'), conn.mailbox_id, conn.mailbox_address, k);
  }
}

/** Interface-specific notes from the compatibility profiles (what surprises people). */
export const NOTES: Record<ChoiceId, string[]> = {
  gmail: [
    'userId must be "me" or the mailbox address; any other user answers 403.',
    'A revoked key or an ended run surfaces in google-api-python-client as RefreshError (it tries to refresh the token on 401).',
    'Media uploads (media_body=...) keep the https scheme: use JSON raw bodies, or put the sandbox behind TLS.',
    'Batch requests, settings.*, watch and resumable uploads are not supported.',
  ],
  graph: [
    '/me is the key’s mailbox; /users/{id} works only for that same mailbox.',
    '$batch is not supported. The SDK retries 429/503; an exhausted action budget will not clear on retry.',
  ],
  imap_smtp: [
    'Turn off “Place a copy in Sent” in mail clients: the simulator already files every SMTP submission in Sent.',
    'Reads are free but audited; every write counts against the run’s action budget.',
    'Sessions end (IMAP BYE / SMTP 421) when the key is revoked or the run finishes.',
  ],
  mcp_http: [
    'Stateless streamable HTTP: no Mcp-Session-Id and no SSE stream; GET and DELETE answer 405.',
    'Tool failures come back as tool results with isError: true and an error code.',
  ],
  mcp_stdio: [
    'The process connects to the database directly: give it the same MAILSIM_DATABASE_URL and MAILSIM_BLOB_DIR as the API server, and run it from the mailsim checkout.',
    'A missing or invalid key makes the process exit with status 2.',
  ],
  rest: [
    'The agent key can only reach its own mailbox; operator routes answer 403.',
    'Writes accept an Idempotency-Key header, so retries are safe.',
  ],
};
