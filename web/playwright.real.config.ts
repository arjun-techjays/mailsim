import { defineConfig, devices } from '@playwright/test';

// Real-stack e2e (integration): no MSW. The Vite dev server proxies /v1 to MAILSIM_API_URL.
// Required env: MAILSIM_API_URL. Optional: MAILSIM_CONTROLLER_KEY, MAILSIM_EVALUATOR_KEY,
// MAILSIM_AGENT_KEY (tests needing a missing key are skipped).
const PORT = Number(process.env.E2E_PORT ?? 5175);
const API = process.env.MAILSIM_API_URL;

export default defineConfig({
  testDir: './e2e/real',
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  timeout: 60_000,
  use: { baseURL: `http://localhost:${PORT}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: API
    ? {
        command: `npx vite --mode development --port ${PORT} --strictPort`,
        url: `http://localhost:${PORT}`,
        reuseExistingServer: false,
        timeout: 60_000,
        env: { MAILSIM_API_URL: API },
      }
    : undefined,
});
