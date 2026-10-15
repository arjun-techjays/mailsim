import type { ConnectionTestResult, RunConnection } from '../api/types';
import { maskKey } from './agentKeys';
import {
  choiceById,
  CHOICES,
  DEFAULT_STDIO_COMMAND,
  gmailRoot,
  hostOf,
  KEY_PLACEHOLDER,
  NOTES,
  shellJoin,
  snippetsFor,
} from './snippets';
import { testHint } from './testHint';

const KEY = 'msk_0123456789ab_secretSECRETsecretSECRETsecret12';

function conn(overrides: Partial<RunConnection> = {}): RunConnection {
  return {
    run_id: 'run_1',
    run_status: 'RUNNING',
    mailbox_id: 'mbx_finance',
    mailbox_address: 'finance@acme.test',
    persona_id: 'finance',
    agent_keys: [],
    interfaces: [
      { interface: 'rest', available: true, base_url: 'http://127.0.0.1:8000/v1' },
      { interface: 'gmail', available: true, base_url: 'http://127.0.0.1:8000/gmail/v1' },
      { interface: 'graph', available: true, base_url: 'http://127.0.0.1:8000/graph/v1.0' },
      { interface: 'mcp_http', available: true, base_url: 'http://127.0.0.1:8000/mcp' },
      { interface: 'mcp_stdio', available: true, command: ['uv', 'run', 'python', '-m', 'mailsim.serve', 'mcp-stdio'] },
      { interface: 'imap', available: true, host: '127.0.0.1', port: 1143, tls: 'none', username: 'finance@acme.test' },
      { interface: 'smtp', available: true, host: '127.0.0.1', port: 1587, tls: 'none', username: 'finance@acme.test' },
    ],
    ...overrides,
  };
}

const code = (choice: Parameters<typeof snippetsFor>[0], c = conn(), key: string | null = KEY) =>
  snippetsFor(choice, c, key)
    .map((s) => s.code)
    .join('\n----\n');

describe('interface choices', () => {
  it('covers every interface with a use-when line, auth and notes', () => {
    expect(CHOICES.map((c) => c.id)).toEqual(['gmail', 'graph', 'imap_smtp', 'mcp_http', 'mcp_stdio', 'rest']);
    for (const c of CHOICES) {
      expect(c.useWhen.length).toBeGreaterThan(20);
      expect(c.auth).toMatch(/key/i);
      expect(NOTES[c.id].length).toBeGreaterThan(0);
    }
    expect(choiceById('graph')?.alias).toBe('Office 365 simulator');
    expect(choiceById('gmail')?.alias).toBe('GSuite simulator');
    expect(choiceById(null)).toBeUndefined();
    expect(choiceById('smoke')).toBeUndefined();
  });
});

describe('snippets', () => {
  it('gmail: static discovery, api_endpoint root and the key as OAuth token', () => {
    const c = code('gmail');
    expect(c).toContain('client_options={"api_endpoint": "http://127.0.0.1:8000/"}');
    expect(c).toContain('static_discovery=True');
    expect(c).toContain('credentials=Credentials(token=AGENT_KEY)');
    expect(c).toContain(`AGENT_KEY = "${KEY}"  # agent key for finance@acme.test`);
    expect(c).toContain(`curl -H 'Authorization: Bearer ${KEY}' \\\n  'http://127.0.0.1:8000/gmail/v1/users/me/messages?q=is:unread'`);
  });

  it('graph: kiota static bearer token, base URL override and allowed host', () => {
    const c = code('graph');
    expect(c).toContain('BASE = "http://127.0.0.1:8000/graph/v1.0"');
    expect(c).toContain('BaseBearerTokenAuthenticationProvider(SandboxKey(AGENT_KEY))');
    expect(c).toContain('adapter.base_url = BASE');
    expect(c).toContain('AllowedHostsValidator(["127.0.0.1:8000"])');
    expect(c).toContain("'http://127.0.0.1:8000/graph/v1.0/me/mailFolders/inbox/messages?$top=5&$select=subject,from'");
  });

  it('imap/smtp: mailbox address as username, key as password, STARTTLS when offered', () => {
    const plain = code('imap_smtp');
    expect(plain).toContain('imaplib.IMAP4("127.0.0.1", 1143)');
    expect(plain).toContain('smtplib.SMTP("127.0.0.1", 1587)');
    expect(plain).toContain('ADDRESS = "finance@acme.test"');
    expect(plain).toContain('imap.login(ADDRESS, AGENT_KEY)');
    expect(plain).not.toContain('starttls');
    const tls = conn();
    tls.interfaces = tls.interfaces.map((e) => (e.interface === 'imap' || e.interface === 'smtp' ? { ...e, tls: 'starttls' } : e));
    const t = code('imap_smtp', tls);
    expect(t).toContain('imap.starttls()');
    expect(t).toContain('    s.starttls()');
    // missing endpoints fall back to the documented defaults
    const bare = code('imap_smtp', conn({ interfaces: [] }));
    expect(bare).toContain('imaplib.IMAP4("127.0.0.1", 1143)');
    expect(bare).toContain('smtplib.SMTP("127.0.0.1", 1587)');
  });

  it('mcp over http: claude mcp add and a JSON config', () => {
    const [cli, json] = snippetsFor('mcp_http', conn(), KEY);
    expect(cli?.code).toBe(`claude mcp add --transport http mailsim 'http://127.0.0.1:8000/mcp' \\\n  --header 'Authorization: Bearer ${KEY}'`);
    expect(JSON.parse(json?.code as string)).toEqual({
      mcpServers: { mailsim: { type: 'http', url: 'http://127.0.0.1:8000/mcp', headers: { Authorization: `Bearer ${KEY}` } } },
    });
  });

  it('mcp over stdio: command from the server, key in MAILSIM_API_KEY only', () => {
    const [cli, json, sh] = snippetsFor('mcp_stdio', conn(), KEY);
    expect(cli?.code).toContain(`claude mcp add mailsim --env MAILSIM_API_KEY='${KEY}'`);
    expect(cli?.code).toContain('-- uv run python -m mailsim.serve mcp-stdio');
    const cfg = JSON.parse(json?.code as string) as { mcpServers: { mailsim: { command: string; args: string[]; env: Record<string, string> } } };
    expect(cfg.mcpServers.mailsim.command).toBe('uv');
    expect(cfg.mcpServers.mailsim.args).toEqual(['run', 'python', '-m', 'mailsim.serve', 'mcp-stdio']);
    expect(cfg.mcpServers.mailsim.env.MAILSIM_API_KEY).toBe(KEY);
    expect(sh?.code).toBe(`MAILSIM_API_KEY='${KEY}' uv run python -m mailsim.serve mcp-stdio`);
    const dflt = snippetsFor('mcp_stdio', conn({ interfaces: [] }), null)[2];
    expect(dflt?.code).toBe(`MAILSIM_API_KEY='${KEY_PLACEHOLDER}' ${DEFAULT_STDIO_COMMAND.join(' ')}`);
  });

  it('rest: mailbox routes and a send example', () => {
    const c = code('rest');
    expect(c).toContain("'http://127.0.0.1:8000/v1/mailboxes/mbx_finance/folders'");
    expect(c).toContain("'http://127.0.0.1:8000/v1/mailboxes/mbx_finance/messages?folder=inbox'");
    expect(c).toContain('"to":["finance@acme.test"]');
  });

  it('uses a placeholder when the key is not revealed', () => {
    for (const ch of CHOICES) {
      const c = code(ch.id, conn(), null);
      expect(c).toContain(KEY_PLACEHOLDER);
      expect(c).not.toContain('msk_');
    }
  });

  it('helpers', () => {
    expect(gmailRoot('http://h:1/gmail/v1/')).toBe('http://h:1/');
    expect(gmailRoot('https://sandbox.example/prefix/gmail/v1')).toBe('https://sandbox.example/prefix/');
    expect(hostOf('http://127.0.0.1:8000/graph/v1.0')).toBe('127.0.0.1:8000');
    expect(hostOf('not a url')).toBe('not a url');
    expect(shellJoin(['python', '-m', 'x y', "it's"])).toBe(`python -m 'x y' 'it'\\''s'`);
    expect(maskKey(KEY)).toBe('msk_0123456789ab_••••••••');
    expect(maskKey('pasted-key')).toBe('••••••••');
  });
});

describe('test hints', () => {
  const r = (over: Partial<ConnectionTestResult>): ConnectionTestResult => ({ interface: 'rest', ok: false, summary: 's', ...over });
  it('maps error codes to next steps', () => {
    expect(testHint(r({ error_code: 'UNAUTHENTICATED' }))).toMatch(/Mint a new agent key/);
    expect(testHint(r({ interface: 'imap', error_code: 'AUTHENTICATIONFAILED' }))).toMatch(/revoked/);
    expect(testHint(r({ error_code: 'RUN_NOT_ACTIVE' }))).toMatch(/Start it/);
    expect(testHint(r({ error_code: 'ErrorAccessDenied' }))).toMatch(/not an agent key/);
    expect(testHint(r({ interface: 'smtp', error_code: 'LISTENER_DOWN' }))).toMatch(/mailsim.serve protocols/);
    expect(testHint(r({ interface: 'mcp_stdio', error_code: 'EXIT' }))).toMatch(/mcp-stdio/);
    expect(testHint(r({ error_code: null }))).toMatch(/server log/);
  });
});
