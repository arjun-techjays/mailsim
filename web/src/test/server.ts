import { setupServer } from 'msw/node';
import { createHandlers } from '../mocks/handlers';
import { createMockDb, type MockDb } from '../mocks/db';

export let mockDb: MockDb = createMockDb();
export const server = setupServer();

export function resetMockDb(): void {
  mockDb = createMockDb();
  server.resetHandlers(...createHandlers(mockDb));
}
resetMockDb();
