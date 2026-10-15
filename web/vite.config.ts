/// <reference types="vitest/config" />
import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/** Strict policy for built bundles (meta fallback; the static host should send it as a header). */
export const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-src 'self'; " +
  "img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'";

/** Headers the static host must send (also applied by `vite preview`). */
export const SECURITY_HEADERS: Record<string, string> = {
  'Content-Security-Policy': `${CSP}; frame-ancestors 'none'`,
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'X-Frame-Options': 'DENY',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), clipboard-read=()',
  'Cross-Origin-Opener-Policy': 'same-origin',
};

// The dev server (HMR, React refresh preamble) needs inline scripts, so the CSP meta is only
// injected into built bundles; e2e runs one project against a strict-CSP build.
function cspMeta(): Plugin {
  return {
    name: 'mailsim-csp-meta',
    apply: 'build',
    transformIndexHtml: () => [
      { tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: CSP }, injectTo: 'head-prepend' },
    ],
  };
}

// Real mode: the dashboard calls the API on its own origin and Vite proxies /v1 to
// MAILSIM_API_URL, so the browser never needs CORS on the API. Mock mode (--mode mock)
// serves the MSW worker and needs no backend at all.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiTarget = env.MAILSIM_API_URL ?? 'http://127.0.0.1:8000';
  // /v1 for the dashboard; /gmail, /upload/gmail, /graph and /mcp so a client running on the
  // dashboard's origin can reach the agent interfaces too (same target as /v1).
  const apiProxy = Object.fromEntries(
    ['/v1', '/gmail', '/upload/gmail', '/graph', '/mcp'].map((p) => [p, { target: apiTarget, changeOrigin: true }]),
  );
  return {
    plugins: [react(), cspMeta()],
    // public/ only holds the MSW worker: serve/copy it in mock mode only.
    publicDir: mode === 'mock' ? 'public' : false,
    server: {
      port: Number(env.PORT ?? 5173),
      strictPort: true,
      proxy: mode === 'mock' ? undefined : apiProxy,
    },
    preview: {
      port: Number(env.PORT ?? 4173),
      strictPort: true,
      headers: SECURITY_HEADERS,
      proxy: mode === 'mock' ? undefined : apiProxy,
    },
    // No production source maps. Mock builds go to a separate directory and must never be deployed.
    build: { sourcemap: false, outDir: mode === 'mock' ? 'dist-mock' : 'dist' },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      restoreMocks: true,
      coverage: {
        provider: 'v8',
        include: ['src/**/*.{ts,tsx}'],
        exclude: [
          'src/api/schema.gen.ts',
          'src/mocks/**',
          'src/main.tsx',
          'src/test/**',
          'src/**/*.test.{ts,tsx}',
          'src/vite-env.d.ts',
        ],
        reporter: ['text', 'text-summary', 'json-summary'],
        thresholds: { perFile: true, lines: 95, branches: 95, functions: 95, statements: 95 },
      },
    },
  };
});
