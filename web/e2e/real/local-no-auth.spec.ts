import { expect, test } from '@playwright/test';

// Real-stack local no-auth check. Start the API with `python -m mailsim.serve serve --local-no-auth`
// (loopback only), copy the launch token from the printed dashboard link, and run
// `npm run e2e:real` with MAILSIM_API_URL and MAILSIM_LOCAL_TOKEN=<token>.
const API = process.env.MAILSIM_API_URL;
const TOKEN = process.env.MAILSIM_LOCAL_TOKEN;
test.skip(!API || !TOKEN, 'set MAILSIM_LOCAL_TOKEN (from the --local-no-auth server output) to run');

test('real server in local no-auth mode: launch link = operator; no token, agent routes and bad keys = 401', async ({
  page,
  request,
}) => {
  await page.goto('/runs');
  await expect(page).toHaveURL(/\/connect$/);
  await expect(page.getByTestId('local-no-auth-link')).toBeVisible();

  await page.goto(`/#local_token=${TOKEN}`);
  await expect(page.getByRole('heading', { level: 1, name: 'Test your email agent' })).toBeVisible();
  expect(new URL(page.url()).hash).toBe('');
  await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
  await expect(page.getByTestId('local-no-auth-banner')).toBeVisible();
  await page.goto('/runs');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByTestId('role-gate')).toHaveCount(0);

  const token = { 'X-Mailsim-Local-Token': TOKEN as string };
  const me = await request.get('/v1/me', { headers: token });
  expect(await me.json()).toMatchObject({ role: 'operator', local_no_auth: true, key_id: null });
  const anon = await request.get('/v1/me');
  expect(anon.status()).toBe(401); // a local process without the token (e.g. the agent) is not the operator
  expect(anon.headers()['x-mailsim-local-no-auth']).toBe('1');
  for (const path of ['/v1/mailboxes/mbx_any/messages', '/v1/mailboxes/mbx_any/folders']) {
    expect((await request.get(path, { headers: token })).status(), path).toBe(401);
  }
  const bad = await request.get('/v1/scenarios', { headers: { authorization: 'Basic eDp5', ...token } });
  expect(bad.status()).toBe(401);
});

test('operator walk in local no-auth mode: home checklist, builder, connect page with the listeners up', async ({
  page,
  request,
}) => {
  const token = { 'X-Mailsim-Local-Token': TOKEN as string };
  await page.goto(`/#local_token=${TOKEN}`);
  await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
  await expect(page.getByTestId('checklist')).toBeVisible();
  await expect(page.getByTestId('step-1')).toBeVisible();
  await expect(page.getByTestId('progress-text')).toContainText(/\d+ of \d+ steps done\./);
  await expect(page.getByTestId('status-pill')).toContainText('IMAP up · SMTP up');

  await page.goto('/scenarios/new');
  await expect(page.getByRole('button', { name: /Blank scenario/ })).toBeVisible();

  // a run to connect to (the operator is controller + evaluator)
  const id = `e2e.noauth-${Date.now().toString(36)}`;
  const source = [
    'schema_version: "1.0"',
    'scenario:',
    `  id: "${id}"`,
    '  version: 1',
    '  title: "Local no-auth walk"',
    '  environment: {seed: 1, clock: {start_at: "2026-10-15T09:00:00Z", mode: virtual}}',
    '  organization: {domain: "example.test"}',
    '  personas:',
    '    - {id: ops, email: "ops@example.test", role: "Ops"}',
    '    - {id: vendor, email: "vendor@example.test", role: "Vendor"}',
    '  mailboxes: [{owner: ops}, {owner: vendor}]',
    '  agent: {mailbox: ops}',
  ].join('\n');
  const published = await request.post('/v1/scenarios', { headers: { ...token, 'Idempotency-Key': `${id}-p` }, data: { source } });
  expect(published.status()).toBe(201);
  const created = await request.post(`/v1/scenarios/${id}/runs`, {
    headers: { ...token, 'Idempotency-Key': `${id}-r` },
    data: { agent_build: 'noauth-walk', start: true },
  });
  expect(created.status()).toBe(201);
  const runId = ((await created.json()) as { run: { id: string } }).run.id;

  await page.goto(`/runs/${runId}/connect`);
  await expect(page.getByRole('heading', { level: 1, name: 'Connect your agent' })).toBeVisible();
  await page.getByRole('radio', { name: /IMAP \/ SMTP/ }).check();
  await expect(page.getByTestId('listener-imap')).toContainText('running');
  await expect(page.getByTestId('listener-smtp')).toContainText('running');
});
