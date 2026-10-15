import { createContext, useContext } from 'react';
import type { Api } from '../api/endpoints';
import type { Draft, FixtureInfo } from './draft';
import type { BuilderMeta } from './help';
import type { ScenarioForm } from './model';
import type { IndexedIssue } from './paths';

export interface BuilderValue {
  api: Api;
  draft: Draft;
  meta: BuilderMeta;
  /** Editing a published scenario: the id (scenario key) is fixed. */
  idLocked: boolean;
  issues: IndexedIssue[];
  /** Mutates a copy of the form. */
  updateForm: (fn: (f: ScenarioForm) => void) => void;
  addFixture: (path: string, b64: string, info: FixtureInfo, owner: string) => void;
  /** Removes an initial message from a mailbox (and its file when nothing else uses it). */
  removeMessage: (owner: string, path: string) => void;
  setInfo: (path: string, info: FixtureInfo) => void;
}

export const BuilderContext = createContext<BuilderValue | null>(null);

export function useBuilder(): BuilderValue {
  const v = useContext(BuilderContext);
  if (!v) throw new Error('useBuilder must be used inside the scenario builder');
  return v;
}

/** Messages of the validation issues reported exactly at `path` (normalized). */
export function useIssues(path: string): string[] {
  const { issues } = useBuilder();
  return issues.filter((i) => i.key === path).map((i) => i.message);
}
