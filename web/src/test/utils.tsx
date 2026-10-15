import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import type { Role } from '../api/types';
import { SessionProvider } from '../auth/SessionProvider';
import type { Session } from '../auth/storage';
import { MOCK_KEYS } from '../mocks/db';
import { makeQueryClient } from '../queryClient';
import { routes } from '../routes';

export const API_BASE = 'http://127.0.0.1:7357';

export function sessionFor(role: Role | 'controllerEmpty'): Session {
  const key = MOCK_KEYS[role];
  return { baseUrl: API_BASE, apiKey: key, role: role === 'controllerEmpty' ? 'controller' : role };
}

export function renderApp(path: string, session: Session | null = null) {
  const queryClient = makeQueryClient();
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const user = userEvent.setup();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <SessionProvider initial={session}>
        <RouterProvider router={router} />
      </SessionProvider>
    </QueryClientProvider>,
  );
  return { ...utils, user, router, queryClient };
}
