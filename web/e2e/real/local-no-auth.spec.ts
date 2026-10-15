import { expect, test } from '@playwright/test';

// Real-stack local no-auth check. Start the API with `python -m mailsim.serve serve --local-no-auth`
// (loopback only) and run `npm run e2e:real` with MAILSIM_API_URL and MAILSIM_EXPECT_LOCAL_NO_AUTH=1.
const API = process.env.MAILSIM_API_URL;
test.skip(!API || process.env.MAILSIM_EXPECT_LOCAL_NO_AUTH !== '1', 'set MAILSIM_EXPECT_LOCAL_NO_AUTH=1 against a --local-no-auth server');

test('real server in local no-auth mode: operator dashboard without a key, agent routes still demand keys', async ({
  page,
  request,
}) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/scenarios$/);
  await expect(page.getByTestId('session-role')).toHaveText('operator (local no-auth)');
  await expect(page.getByTestId('local-no-auth-banner')).toBeVisible();
  await page.goto('/runs');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByTestId('role-gate')).toHaveCount(0);

  const me = await request.get('/v1/me');
  expect(me.status()).toBe(200);
  expect(await me.json()).toMatchObject({ role: 'operator', local_no_auth: true, key_id: null });
  for (const path of ['/v1/mailboxes/mbx_any/messages', '/v1/mailboxes/mbx_any/folders']) {
    const r = await request.get(path);
    expect(r.status(), path).toBe(401);
  }
  const bad = await request.get('/v1/scenarios', { headers: { authorization: 'Bearer msk_bogus' } });
  expect(bad.status()).toBe(401);
});
