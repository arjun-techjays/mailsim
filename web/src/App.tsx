import { QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { SessionProvider } from './auth/SessionProvider';
import type { Session } from './auth/storage';
import { makeQueryClient } from './queryClient';
import { routes } from './routes';

export function App({ initialSession }: { initialSession?: Session | null }) {
  const [queryClient] = useState(makeQueryClient);
  const [router] = useState(() => createBrowserRouter(routes));
  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider initial={initialSession}>
        <RouterProvider router={router} />
      </SessionProvider>
    </QueryClientProvider>
  );
}
