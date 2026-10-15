// Personas and scenario events for the editor's pickers. Controllers (and the local operator)
// read the published scenario document; an evaluator-only session cannot, so it falls back to
// the personas recorded in the newest run's state. Without either, fields stay free text.
import { useQuery } from '@tanstack/react-query';
import { hasRole, useApi, useRole } from '../auth/session';
import type { PersonaOption } from './CheckBuilder';

export interface ScenarioContext {
  personas: PersonaOption[];
  events: string[];
  /** Labels seen on messages of the newest run (suggestions for `label`). */
  labels: string[];
  source: 'scenario' | 'run' | 'none';
  loading: boolean;
}

type Row = Record<string, unknown>;
const rows = (v: unknown): Row[] => (Array.isArray(v) ? v.filter((x): x is Row => !!x && typeof x === 'object') : []);
const s = (v: unknown) => (typeof v === 'string' ? v : '');

export function personaOptions(list: unknown): PersonaOption[] {
  return rows(list)
    .map((p) => ({
      id: s(p.id),
      email: s(p.email),
      label: [s(p.name), s(p.role)].filter(Boolean).join(', ') || s(p.id),
    }))
    .filter((p) => p.id);
}

/** Distinct labels on the messages of a run state snapshot, sorted. */
export function labelsOf(messages: unknown): string[] {
  const all = rows(messages).flatMap((m) => (Array.isArray(m.labels) ? m.labels : []));
  return [...new Set(all.filter((l): l is string => typeof l === 'string'))].sort();
}

/** `latestVersion` 0 = the scenario has no published version. */
export function useScenarioContext(scenarioKey: string, latestVersion: number): ScenarioContext {
  const api = useApi();
  const controller = hasRole(useRole(), ['controller']);

  const doc = useQuery({
    queryKey: ['scenario-doc', scenarioKey, latestVersion],
    queryFn: () => api.scenarioVersionDocument(scenarioKey, latestVersion),
    enabled: controller && latestVersion > 0,
    retry: false,
  });
  const runs = useQuery({
    queryKey: ['runs', 'for-scenario', scenarioKey],
    queryFn: () => api.listRuns(null, { scenario_key: scenarioKey }),
    retry: false,
  });
  const runId = runs.data?.items[0]?.id;
  const state = useQuery({
    queryKey: ['run', runId, 'state'],
    queryFn: () => api.state(runId as string),
    enabled: !!runId,
    retry: false,
  });

  const labels = labelsOf(state.data?.messages);
  if (controller) {
    const body = (doc.data?.document?.scenario ?? {}) as Row;
    const personas = personaOptions(body.personas);
    return {
      personas,
      events: rows(body.events).map((e) => s(e.id)).filter(Boolean),
      labels,
      source: personas.length > 0 ? 'scenario' : 'none',
      loading: doc.isLoading,
    };
  }
  const personas = personaOptions(state.data?.personas);
  return {
    personas,
    events: [],
    labels,
    source: personas.length > 0 ? 'run' : 'none',
    loading: runs.isLoading || state.isLoading,
  };
}
