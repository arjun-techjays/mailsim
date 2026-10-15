// Friendly aliases over the generated OpenAPI types (src/api/schema.gen.ts, `npm run gen:api`).
import type { components } from './schema.gen';

type S = components['schemas'];

export type ScenarioOut = S['ScenarioOut'];
export type ScenarioVersionOut = S['ScenarioVersionOut'];
export type AnswerKeyOut = S['AnswerKeyOut'];
export type RunCreate = S['RunCreate'];
export type RunOut = S['RunOut'];
export type RunCreated = S['RunCreated'];
export type AgentCredential = S['AgentCredential'];
export type EventOut = S['EventOut'];
export type EventPage = S['EventPage'];
export type ActionOut = S['ActionOut'];
export type ReplayOut = S['ReplayOut'];
export type ArtifactOut = S['ArtifactOut'];
export type EvaluationRequest = S['EvaluationRequest'];
export type EvaluationReport = S['EvaluationReport'];
export type AssertionOutcome = S['AssertionOutcome'];
export type AssertionDiff = S['AssertionDiff'];
export type EvidenceRef = S['EvidenceRef'];
export type CategoryMetrics = S['CategoryMetrics'];
export type BuildComparison = S['BuildComparison'];
export type TrialStats = S['TrialStats'];
export type InjectRequest = S['InjectRequest'];
export type InjectMessage = S['InjectMessage'];
export type InjectResult = S['InjectResult'];
export type AdvanceRequest = S['AdvanceRequest'];
export type ClockResult = S['ClockResult'];
export type CanonicalMessage = S['CanonicalMessage'];
export type AttachmentRef = S['AttachmentRef'];
export type Address = S['Address'];
export type MessagePage = S['MessagePage'];
export type FolderOut = S['FolderOut'];
export type ErrorEnvelope = S['ErrorEnvelope'];
export type ErrorBody = S['ErrorBody'];
export type MeOut = S['MeOut'];
export type RunPage = S['RunPage'];
export type ReportFormat = 'json' | 'junit' | 'markdown';

/** `operator` = local no-auth mode (`serve --local-no-auth`): combined controller + evaluator. */
export type Role = 'controller' | 'evaluator' | 'agent' | 'operator';
/** Roles a key can carry (`operator` has no key). */
export type KeyRole = Exclude<Role, 'operator'>;

