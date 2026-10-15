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
