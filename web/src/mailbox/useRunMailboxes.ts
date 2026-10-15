// Everything the mailbox browser shows in its left column: every persona mailbox of the run,
// who owns it, which one the agent under test uses, and per-folder counts.
//  - mailbox list: GET /v1/runs/{id}/mailboxes (owner = persona id)
//  - personas, folders and counts: the run state snapshot (GET /v1/runs/{id}/state)
//  - agent's mailbox: the `run.created` event's config.agent.mailbox_owner
//  - display names: the scenario document (controllers only; evaluators see role + id)
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import type { CanonicalMessage, RunOut } from '../api/types';
import { hasRole, useApi, useRole } from '../auth/session';
import { runKeys } from '../run/keys';
import type { ParsedSnapshot } from './snapshot';

export interface BrowserFolder {
  id: string;
  name: string;
  systemType: string | null;
  total: number;
  unread: number;
}

export interface BrowserMailbox {
  id: string;
  email: string;
  ownerId: string;
  /** Display name from the scenario (controllers), else ''. */
  name: string;
  /** Persona role, e.g. "Finance Manager" ('' when unknown). */
  role: string;
  isAgent: boolean;
  folders: BrowserFolder[];
  total: number;
  unread: number;
}

type Row = Record<string, unknown>;
const obj = (v: unknown): Row => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Row) : {});

export const isUnread = (m: CanonicalMessage) => !(m.flags ?? []).includes('SEEN') && !m.is_draft;

const FOLDER_ORDER = ['inbox', 'drafts', 'sent', 'archive', 'trash'];
const folderRank = (f: { systemType: string | null; name: string }) => {
  const i = FOLDER_ORDER.indexOf(f.systemType ?? '');
  return i === -1 ? FOLDER_ORDER.length : i;
};

export function buildMailboxes(
  snap: ParsedSnapshot,
  listed: { id: string; email: string; owner_id: string }[] | undefined,
  agentOwner: string | null,
  names: Record<string, string>,
): BrowserMailbox[] {
  const source = listed && listed.length > 0 ? listed : snap.mailboxes;
  const out = source.map((mb) => {
    const owner = mb.owner_id || (snap.mailboxes.find((m) => m.id === mb.id)?.owner_id ?? '');
    const persona = snap.personas.find((p) => p.id === owner);
    const messages = snap.messages.filter((m) => m.mailbox_id === mb.id);
    const folders = snap.folders
      .filter((f) => f.mailbox_id === mb.id)
      .map((f) => {
        const inFolder = messages.filter((m) => m.folder === f.name);
        return {
          id: f.id,
          name: f.name,
          systemType: f.system_type ?? null,
          total: inFolder.length,
          unread: inFolder.filter(isUnread).length,
        };
      })
      .sort((a, b) => folderRank(a) - folderRank(b) || a.name.localeCompare(b.name));
    return {
      id: mb.id,
      email: mb.email,
      ownerId: owner,
      name: names[owner] ?? '',
      role: persona?.role ?? '',
      isAgent: !!agentOwner && owner === agentOwner,
      folders,
      total: messages.length,
      unread: messages.filter(isUnread).length,
    };
  });
  return out.sort((a, b) => Number(b.isAgent) - Number(a.isAgent) || a.ownerId.localeCompare(b.ownerId));
}

/** Persona display names from a scenario document (`scenario.personas[].name`). */
export function personaNames(document: unknown): Record<string, string> {
  const personas = obj(obj(document).scenario).personas;
  const out: Record<string, string> = {};
  if (Array.isArray(personas)) {
    for (const p of personas.map(obj)) {
      if (typeof p.id === 'string' && typeof p.name === 'string' && p.name) out[p.id] = p.name;
    }
  }
  return out;
}

export function useRunMailboxes(run: RunOut, snapshot: ParsedSnapshot | undefined) {
  const api = useApi();
  const controller = hasRole(useRole(), ['controller']);
  const listed = useQuery({ queryKey: runKeys.mailboxes(run.id), queryFn: () => api.runMailboxes(run.id) });
  const owner = useQuery({
    queryKey: runKeys.agentOwner(run.id),
    queryFn: async () => {
      const page = await api.events(run.id, null, 1, { type: 'run.created' });
      const cfg = obj(obj(page.items[0]?.payload).config);
      const v = obj(cfg.agent).mailbox_owner;
      return typeof v === 'string' ? v : null;
    },
    staleTime: Infinity,
  });
  const version = run.scenario_version;
  const doc = useQuery({
    queryKey: ['scenario-doc', run.scenario_key, version],
    queryFn: () => api.scenarioVersionDocument(run.scenario_key as string, version as number),
    enabled: controller && !!run.scenario_key && version != null,
    staleTime: Infinity,
    retry: false,
  });
  const agentOwner = owner.data ?? (obj(obj(obj(doc.data?.document).scenario).agent).mailbox as string | undefined) ?? null;
  const mailboxes = useMemo(
    () => (snapshot ? buildMailboxes(snapshot, listed.data, agentOwner, personaNames(doc.data?.document)) : undefined),
    [snapshot, listed.data, agentOwner, doc.data],
  );
  return { mailboxes, error: listed.error };
}

/** "Asha Rao (Finance Manager)" / "Vendor" / "per_vendor". */
export function mailboxTitle(mb: BrowserMailbox): string {
  if (mb.name && mb.role) return `${mb.name} (${mb.role})`;
  return mb.name || mb.role || mb.ownerId || mb.email;
}
