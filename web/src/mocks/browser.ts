import { setupWorker } from 'msw/browser';
import { createMockDb } from './db';
import { createHandlers } from './handlers';

const db = createMockDb();
db.localNoAuth = import.meta.env.VITE_MOCK_LOCAL_NO_AUTH === '1';
export const worker = setupWorker(...createHandlers(db));
