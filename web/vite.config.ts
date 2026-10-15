/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Real mode: the dashboard calls the API on its own origin and Vite proxies /v1 to
// MAILSIM_API_URL, so the browser never needs CORS on the API. Mock mode (--mode mock)
// serves the MSW worker and needs no backend at all.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiTarget = env.MAILSIM_API_URL ?? 'http://127.0.0.1:8000';
  return {
    plugins: [react()],
    server: {
      port: Number(env.PORT ?? 5173),
      strictPort: true,
      proxy: mode === 'mock' ? undefined : { '/v1': { target: apiTarget, changeOrigin: true } },
    },
    build: { sourcemap: true },
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
          'src/mocks/browser.ts',
          'src/main.tsx',
          'src/test/**',
          'src/**/*.test.{ts,tsx}',
          'src/vite-env.d.ts',
        ],
        reporter: ['text', 'text-summary', 'json-summary'],
        thresholds: { lines: 95, branches: 95, functions: 95, statements: 95 },
      },
    },
  };
});
