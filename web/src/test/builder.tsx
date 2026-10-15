// Helpers for the scenario builder tests (UX-F1).
import { screen, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import type { KeyRole } from '../api/types';
import { server } from './server';
import { renderApp, sessionFor } from './utils';

export const SLOW = { timeout: 4000 };

export async function openNew(template = false, role: KeyRole = 'controller') {
  const r = renderApp('/scenarios/new', sessionFor(role));
  await r.user.click(await screen.findByRole('button', { name: template ? /Finance example/ : /Blank scenario/ }));
  return r;
}

export async function goSection(user: UserEvent, name: string) {
  const nav = screen.getByRole('navigation', { name: 'Scenario sections' });
  const escaped = name.replace(/[.*+?^${}()|[\]\\&]/g, '\\$&');
  await user.click(within(nav).getByRole('button', { name: new RegExp(`^\\d+\\. ${escaped}`) }));
  return within(screen.getByRole('region', { name }));
}

export const waitValid = () => screen.findByText('Valid: ready to save', {}, SLOW);
export const waitInvalid = () => screen.findByText(/^\d+ problems? to fix$/, {}, SLOW);

/** Collects JSON bodies of requests whose URL ends with `suffix`. */
export function captureBodies(suffix: string): unknown[] {
  const bodies: unknown[] = [];
  server.events.on('request:start', async ({ request }) => {
    if (request.method === 'POST' && new URL(request.url).pathname.endsWith(suffix)) bodies.push(await request.clone().json());
  });
  return bodies;
}
