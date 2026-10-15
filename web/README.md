# mailsim dashboard (`web/`)

Operator dashboard for the mailsim REST API (spec §7, §8.4, §9). Vite + React 19 + TypeScript
(strict) + TanStack Query + react-router. API types are generated from the frozen contract
`../openapi/openapi.json`.

## Commands

| Command | What it does |
| --- | --- |
| `npm ci` | Clean install (Node 22, npm 10; `.npmrc` sets `legacy-peer-deps`, see below) |
| `npm run dev:mock` | Dev server with the MSW mock API (no backend needed) on http://localhost:5173 |
| `MAILSIM_API_URL=http://127.0.0.1:8000 npm run dev` | Dev server against a real API (Vite proxies `/v1` to `MAILSIM_API_URL`) |
| `npm run gen:api` | Regenerate `src/api/schema.gen.ts` from `../openapi/openapi.json` (commit the result) |
| `npm run lint` / `npm run typecheck` | ESLint (incl. `jsx-a11y` strict) / `tsc --noEmit` |
| `npm test` | Vitest + Testing Library with v8 coverage (thresholds: 95% lines/branches/functions/statements) |
| `npm run build` | Type-check and production build to `dist/` |
| `npm run e2e` | Playwright against the MSW-mocked dev server (started automatically on port 5174) |
| `npm run e2e:real` | Playwright against a real API, no MSW (see below) |

First-time Playwright setup: `npx playwright install chromium`.

### Mock mode

`npm run dev:mock` serves the app with an in-browser MSW worker that implements the contract
(`src/mocks/handlers.ts`, fixtures in `src/mocks/db.ts`) including the role guards. Mock keys:

| Key | Role |
| --- | --- |
| `mk_controller` | controller (project with two scenarios and run `run_demo`) |
| `mk_controller_empty` | controller in an empty project |
| `mk_evaluator` | evaluator |
| `mk_agent` | agent bound to `run_demo` / mailbox `mbx_finance` |

Fault injection: scenario search `boom` returns a 500 envelope, `slow` never answers (the client
times out after `VITE_REQUEST_TIMEOUT_MS`, 4 s in mock mode), run `run_boom` returns 500.
Mock state lives in memory and resets on a full page reload.

### Real mode and `e2e:real`

```bash
MAILSIM_API_URL=http://127.0.0.1:8000 \
MAILSIM_CONTROLLER_KEY=... MAILSIM_EVALUATOR_KEY=... MAILSIM_AGENT_KEY=... \
npm run e2e:real
```

`playwright.real.config.ts` starts Vite in development mode with the `/v1` proxy (so the API needs
no CORS) and runs `e2e/real/*.spec.ts`. Without `MAILSIM_API_URL` every test is skipped; tests that
need a missing key are skipped individually. Keep the base URL on the Connect page as the dashboard
origin to use the proxy.

## Security and privacy rules

- API keys are held in memory and `sessionStorage` only (cleared on Disconnect or tab close), never
  `localStorage`, cookies or URLs. Requests use `credentials: 'omit'` and `no-referrer`. ESLint bans
  `localStorage`, `innerHTML` and `dangerouslySetInnerHTML` in `src/`.
- Agent credentials returned by run creation are shown once, kept only in component state, and
  never cached or persisted.
- Email bodies: plain text by default. HTML is rendered only inside `<iframe sandbox="" srcdoc>` with
  a CSP that blocks scripts and all network loads (`src/components/SafeEmailBody.tsx`).
- Answer-key and evaluation views are only rendered (and only requested) with an evaluator key;
  the API remains the enforcement point and 403 envelopes are displayed with their request id.

## Role detection

The v1 contract has no "who am I" endpoint. The Connect page infers the role from the route guards:
`GET /v1/scenarios` (403 means agent), then `GET /v1/scenarios/__mailsim_role_probe__/answer-keys`
(403 means controller, 200/404 means evaluator).

## Notes

- `.npmrc` sets `legacy-peer-deps=true`: npm 10 crashes (`Cannot read properties of null (reading
  'edgesOut')`) resolving vitest 4's optional peer graph. All required peers are listed explicitly.
- Coverage excludes generated types (`src/api/schema.gen.ts`), mocks (`src/mocks/**`), test
  helpers and the `main.tsx` bootstrap.
