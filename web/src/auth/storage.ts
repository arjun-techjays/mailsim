// Session persistence: memory + sessionStorage ONLY (cleared when the tab closes).
// API keys are never written to localStorage, cookies or URLs.
import type { Role } from '../api/types';

export interface Session {
  baseUrl: string;
  apiKey: string;
  role: Role;
}

export const SESSION_STORAGE_KEY = 'mailsim.session';
const ROLES: readonly Role[] = ['controller', 'evaluator', 'agent'];

function store(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function loadSession(): Session | null {
  const raw = store()?.getItem(SESSION_STORAGE_KEY);
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as Partial<Session>;
    if (typeof v.baseUrl === 'string' && typeof v.apiKey === 'string' && v.apiKey && ROLES.includes(v.role as Role)) {
      return { baseUrl: v.baseUrl, apiKey: v.apiKey, role: v.role as Role };
    }
  } catch {
    // corrupted entry: ignore
  }
  return null;
}

export function saveSession(session: Session | null): void {
  const s = store();
  if (!s) return;
  try {
    if (session) s.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
    else s.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // storage full / disabled: keep the session in memory only
  }
}

// ---- recently opened runs (ids only, no credentials) -------------------------------------
export interface RecentRun {
  id: string;
  scenarioKey: string | null;
}
const RECENT_KEY = 'mailsim.recentRuns';

export function loadRecentRuns(): RecentRun[] {
  try {
    const v = JSON.parse(store()?.getItem(RECENT_KEY) ?? '[]') as unknown;
    return Array.isArray(v) ? (v.filter((x) => x && typeof x.id === 'string') as RecentRun[]) : [];
  } catch {
    return [];
  }
}

export function rememberRun(run: RecentRun): void {
  const next = [run, ...loadRecentRuns().filter((r) => r.id !== run.id)].slice(0, 10);
  try {
    store()?.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // ignore
  }
}
