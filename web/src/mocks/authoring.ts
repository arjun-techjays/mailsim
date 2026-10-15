// Shared authoring mocks: GET /v1/meta/authoring and the /v1/tools YAML converters.
// The meta fixture (fixtures/authoringMeta.json) is generated from the real pydantic models
// (scenario JSON schema, answer-key schema, check schemas and enums); regenerate it when they
// change. Intended as the single shared mock for every workstream (register once).
import { http, type HttpHandler } from 'msw';
import type { AuthoringMeta } from '../api/types';
import type { KeyInfo } from './db';
import meta from './fixtures/authoringMeta.json';
import { fromYaml, toYaml, YamlError } from './miniYaml';

export const AUTHORING_META = meta as unknown as AuthoringMeta;

type Guard = 'controller' | 'evaluator' | 'inspector' | 'agent';
export interface AuthoringHelpers {
  auth: (request: Request, guard: Guard) => KeyInfo | Response;
  fail: (status: number, code: string, message: string, details?: Record<string, unknown>) => Response;
  ok: (body: unknown, status?: number) => Response;
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

export function createAuthoringHandlers({ auth, fail, ok }: AuthoringHelpers): HttpHandler[] {
  return [
    http.get('*/v1/meta/authoring', ({ request }) => {
      const who = auth(request, 'inspector');
      return who instanceof Response ? who : ok(AUTHORING_META);
    }),
    http.post('*/v1/tools/json-to-yaml', async ({ request }) => {
      const who = auth(request, 'inspector');
      if (who instanceof Response) return who;
      const body = (await request.json()) as { document?: unknown };
      if (!isObj(body.document)) return fail(400, 'INVALID_REQUEST', 'document must be an object');
      return ok({ source: toYaml(body.document) });
    }),
    http.post('*/v1/tools/yaml-to-json', async ({ request }) => {
      const who = auth(request, 'inspector');
      if (who instanceof Response) return who;
      const body = (await request.json()) as { source?: string };
      try {
        const doc = fromYaml(body.source ?? '');
        if (!isObj(doc)) return ok({ document: null, error: { path: '', message: 'the document must be a mapping (key: value)' } });
        return ok({ document: doc, error: null });
      } catch (e) {
        const line = e instanceof YamlError ? e.line : 0;
        return ok({ document: null, error: { path: `line ${line}`, message: (e as Error).message } });
      }
    }),
  ];
}
