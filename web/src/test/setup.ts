import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { resetLaunchToken } from '../auth/launchToken';
import { server, resetMockDb } from './server';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  resetMockDb();
  sessionStorage.clear();
  resetLaunchToken();
  window.history.replaceState(null, '', '/');
});
afterAll(() => server.close());
