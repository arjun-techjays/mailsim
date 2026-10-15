import { defineConfig, devices } from '@playwright/test';

// Mocked e2e (no backend):
//  - "chromium": Vite dev server in `mock` mode (MSW service worker).
//  - "csp-build": production-style mock build served by `vite preview` with the strict CSP
//    meta tag and the full security header set, proving the bundle works under that policy.
//  - "local-no-auth": mock API behaving like `serve --local-no-auth` (VITE_MOCK_LOCAL_NO_AUTH=1).
const PORT = Number(process.env.E2E_PORT ?? 5174);
const CSP_PORT = Number(process.env.E2E_CSP_PORT ?? 4174);
const NOAUTH_PORT = Number(process.env.E2E_NOAUTH_PORT ?? 5176);

export default defineConfig({
  testDir: './e2e',
  testIgnore: ['real/**'],
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: [['list'], ['html', { open: 'never' }]],
  timeout: 30_000,
  expect: { timeout: 7_000 },
  use: {
    trace: 'retain-on-failure',
    permissions: ['clipboard-read', 'clipboard-write'],
  },
  projects: [
    {
      name: 'chromium',
      testIgnore: ['csp/**', 'real/**', 'local-no-auth/**'],
      use: { ...devices['Desktop Chrome'], baseURL: `http://localhost:${PORT}` },
    },
    {
      name: 'csp-build',
      testMatch: ['csp/**/*.spec.ts'],
      use: { ...devices['Desktop Chrome'], baseURL: `http://localhost:${CSP_PORT}` },
    },
    {
      name: 'local-no-auth',
      testMatch: ['local-no-auth/**/*.spec.ts'],
      use: { ...devices['Desktop Chrome'], baseURL: `http://localhost:${NOAUTH_PORT}` },
    },
  ],
  webServer: [
    {
      command: `npx vite --mode mock --port ${PORT} --strictPort`,
      url: `http://localhost:${PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `npx vite build --mode mock && npx vite preview --mode mock --port ${CSP_PORT} --strictPort`,
      url: `http://localhost:${CSP_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `npx vite --mode mock --port ${NOAUTH_PORT} --strictPort`,
      url: `http://localhost:${NOAUTH_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: { VITE_MOCK_LOCAL_NO_AUTH: '1' },
    },
  ],
});
