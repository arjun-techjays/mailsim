# mailsim dashboard (`web/`)

Operator dashboard for the mailsim REST API (spec §7, §8.4, §9), for **controller and evaluator
keys only**. Agent keys belong to the agent under test: agent reads are recorded as agent actions
and can arm `after_agent_action` triggers, so a human using one would change the run being
evaluated. With an agent key the dashboard shows an explanatory page and makes no data requests. Vite + React 19 + TypeScript
(strict) + TanStack Query + react-router. API types are generated from the frozen contract
`../openapi/openapi.json`.

## Commands

| Command | What it does |
| --- | --- |
| `npm ci` | Clean install (Node 22, npm 10; `.npmrc` sets `legacy-peer-deps`, see below) |
| `npm run dev:mock` | Dev server with the MSW mock API (no backend needed) on http://127.0.0.1:5173 |
| `MAILSIM_API_URL=http://127.0.0.1:8000 npm run dev` | Dev server against a real API (Vite proxies `/v1`, `/gmail`, `/upload/gmail`, `/graph` and `/mcp` to `MAILSIM_API_URL`) |
| `npm run gen:api` | Regenerate `src/api/schema.gen.ts` from `../openapi/openapi.json` (commit the result) |
| `npm run lint` / `npm run typecheck` | ESLint (incl. `jsx-a11y` strict) / `tsc --noEmit` |
| `npm test` | Vitest + Testing Library with v8 coverage (thresholds: 95% lines/branches/functions/statements) |
| `npm run build` | Type-check and production build to `dist/` (no source maps, strict CSP meta tag) |
| `npm run e2e` | Playwright, two projects: `chromium` against the MSW-mocked dev server (port 5174) and `csp-build` against a mock build served by `vite preview` with the strict CSP and security headers (port 4174) |
| `npm run e2e:real` | Playwright against a real API, no MSW (see below) |

First-time Playwright setup: `npx playwright install chromium`.

The dev and preview servers listen on **127.0.0.1 only** (`server.host` / `preview.host` in
`vite.config.ts`) and the proxies send no `X-Forwarded-*` headers. Do not pass `--host` (or
`--host 0.0.0.0`) while a real API is behind the proxy: anyone on the network could then reach
the API, the agent interfaces and, in local no-auth mode, the operator routes through it.

### Mock mode

`npm run dev:mock` serves the app with an in-browser MSW worker that implements the contract
(`src/mocks/handlers.ts`, fixtures in `src/mocks/db.ts`) including the role guards. Mock keys:

| Key | Role |
| --- | --- |
| `mk_controller` | controller (project with two scenarios and run `run_demo`) |
| `mk_controller_empty` | controller in an empty project |
| `mk_evaluator` | evaluator |
| `mk_agent` | agent (dashboard shows the agent-key page) |
| `mk_controller_revocable` | controller whose key is "revoked" (401) after role detection |

Fault injection: scenario search `boom` returns a 500 envelope, `slow` never answers (the client
times out after `VITE_REQUEST_TIMEOUT_MS`, 4 s in mock mode), run `run_boom` returns 500, run
`run_empty` has no events and no evaluations. Mock state lives in memory and resets on a full page
reload. The mock follows the generated schemas, including `GET /v1/me` (MeOut), `GET /v1/runs` (RunPage),
event filters `type`/`actor`/`epoch`, ActionOut `id`/`virtual_time`/`seq_start`/`seq_end`,
`ClockResult.persona_replies` and the evaluation report export (json/junit/markdown). It has no
agent-facing mailbox routes because the dashboard never uses agent keys.

> **Never deploy a `--mode mock` build.** It contains the MSW worker and fake data, and is written
> to `dist-mock/` (not `dist/`) so it cannot be mistaken for the production bundle. CI/deploy
> must only publish `dist/` from `npm run build`.

### Real mode and `e2e:real`

```bash
MAILSIM_API_URL=http://127.0.0.1:8000 \
MAILSIM_CONTROLLER_KEY=... MAILSIM_EVALUATOR_KEY=... MAILSIM_AGENT_KEY=... \
npm run e2e:real
```

It creates one run through the UI and evaluations of that run, and otherwise only reads. It covers
connect + role via `/v1/me` for all three keys, the agent-key page (no requests), scenarios, start
run, runs list, run detail (advance clock, trace filter, state mailbox inspector), evaluation with
per-assertion diff and report download, and build comparison. An agent key can be minted with
`POST /v1/scenarios/{key}/runs` (controller) using `agent_credentials[0].api_key`.

`playwright.real.config.ts` starts Vite in development mode with the `/v1` proxy (so the API needs
no CORS) and runs `e2e/real/*.spec.ts`. Without `MAILSIM_API_URL` every test is skipped; tests that
need a missing key are skipped individually. Keep the base URL on the Connect page as the dashboard
origin to use the proxy.

## Deployment: security headers

`npm run build` injects this CSP as a `<meta>` fallback (the dev server omits it because HMR and the
React refresh preamble need inline scripts):

```
default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-src 'self';
img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'
```

The static host must send these response headers (the same set is applied by `vite preview`, see
`SECURITY_HEADERS` in `vite.config.ts`):

| Header | Value |
| --- | --- |
| `Content-Security-Policy` | the policy above plus `; frame-ancestors 'none'` (not settable via meta) |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `X-Frame-Options` | `DENY` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), clipboard-read=()` |
| `Cross-Origin-Opener-Policy` | `same-origin` |

`connect-src 'self'` means the API must be served from the dashboard's origin (reverse proxy
`/v1`), or the deployment must add the API origin to `connect-src`.

## Security and privacy rules

- API keys are held in memory and `sessionStorage` only (cleared on Disconnect or tab close), never
  `localStorage`, cookies or URLs. Requests use `credentials: 'omit'` and `no-referrer`. ESLint bans
  `localStorage`, `innerHTML` and `dangerouslySetInnerHTML` in `src/`.
- Agent credentials returned by run creation are shown once, kept only in component state, and
  never cached or persisted.
- Email bodies: plain text by default. HTML is first sanitized in an inert `DOMParser` document
  (removes `script`, `meta`, `base`, `link`, `object`, `embed`, `iframe`, `frame`, unwraps `form`,
  strips `on*`/`target` attributes and every URL attribute except `data:image/` sources; link URLs
  are shown as text), then rendered only inside `<iframe sandbox="" srcdoc>` with a CSP that blocks
  scripts and all network loads (`src/components/emailHtml.ts`, `SafeEmailBody.tsx`).
- The Connect page only accepts a bare origin: http(s), no credentials/path/query/fragment, and
  https unless the host is `localhost`, `127.0.0.1` or `[::1]`. A different API origin is shown
  plainly. A 401 during a session shows a "Reconnect" prompt.
- Answer-key and evaluation views are only rendered (and only requested) with an evaluator key;
  the API remains the enforcement point and 403 envelopes are displayed with their request id.

## Role detection

`GET /v1/me` is used when available (its `role` field). If it answers 404/405/501 the role is
inferred from the route guards: `GET /v1/scenarios` (403 means agent), then
`GET /v1/scenarios/__mailsim_role_probe__/answer-keys` (403 means controller, 200/404 means
evaluator). `GET /v1/runs` is used for the run list when available, otherwise the Runs page falls
back to opening by id plus the runs opened in this tab.

## Notes

- `.npmrc` sets `legacy-peer-deps=true`: npm 10 crashes (`Cannot read properties of null (reading
  'edgesOut')`) resolving vitest 4's optional peer graph. All required peers are listed explicitly.
- Coverage thresholds are enforced per file (`perFile: true`). Coverage excludes generated types (`src/api/schema.gen.ts`), mocks (`src/mocks/**`), test
  helpers and the `main.tsx` bootstrap.
