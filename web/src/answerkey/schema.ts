// Helpers over the pydantic-generated JSON Schemas served by GET /v1/meta/authoring
// (`check_schema` per assertion type). Only the constructs pydantic emits are handled:
// $ref into $defs, allOf with a single member, anyOf [X, null] for optional fields, enum,
// arrays, typed objects and free-form objects (additionalProperties).

export type JsonSchema = Record<string, unknown>;

export type Kind = 'object' | 'map' | 'array' | 'enum' | 'string' | 'integer' | 'number' | 'boolean' | 'unknown';

const isObj = (v: unknown): v is JsonSchema => !!v && typeof v === 'object' && !Array.isArray(v);

/** Name of the $defs entry a schema points at (e.g. "Count", "MessageWhere"). */
export function refName(s: JsonSchema): string | undefined {
  const ref = typeof s.$ref === 'string' ? s.$ref : undefined;
  if (ref) return ref.split('/').pop();
  const all = Array.isArray(s.allOf) && s.allOf.length === 1 && isObj(s.allOf[0]) ? s.allOf[0] : undefined;
  return all ? refName(all) : undefined;
}

/** Follows $ref (#/$defs/X) and single-member allOf; the result keeps sibling keys (default, description). */
export function resolve(s: JsonSchema, root: JsonSchema): JsonSchema {
  const { $ref, allOf, ...rest } = s;
  if (typeof $ref === 'string') {
    const name = $ref.split('/').pop() as string;
    const defs = isObj(root.$defs) ? root.$defs : {};
    const target = isObj(defs[name]) ? (defs[name] as JsonSchema) : {};
    return { ...resolve(target, root), ...rest };
  }
  if (Array.isArray(allOf) && allOf.length === 1 && isObj(allOf[0])) {
    return { ...resolve(allOf[0], root), ...rest };
  }
  return s;
}

/** anyOf [X, {type: null}] -> X (resolved), optional. Anything else is returned resolved. */
export function unwrap(s: JsonSchema, root: JsonSchema): { schema: JsonSchema; optional: boolean; name?: string } {
  if (Array.isArray(s.anyOf)) {
    const members = s.anyOf.filter(isObj);
    const nonNull = members.filter((m) => m.type !== 'null');
    if (nonNull.length === 1 && members.length === 2) {
      const inner = nonNull[0] as JsonSchema;
      const { anyOf: _anyOf, ...siblings } = s;
      return { schema: { ...resolve(inner, root), ...siblings }, optional: true, name: refName(inner) };
    }
  }
  return { schema: resolve(s, root), optional: false, name: refName(s) };
}

export function kindOf(s: JsonSchema): Kind {
  if (Array.isArray(s.enum)) return 'enum';
  if (s.type === 'object' || isObj(s.properties)) return isObj(s.properties) ? 'object' : 'map';
  if (s.type === 'array') return 'array';
  if (s.type === 'string' || s.type === 'integer' || s.type === 'number' || s.type === 'boolean') return s.type;
  return 'unknown';
}

export function properties(s: JsonSchema): [string, JsonSchema][] {
  return isObj(s.properties) ? Object.entries(s.properties).filter((e): e is [string, JsonSchema] => isObj(e[1])) : [];
}

export function requiredKeys(s: JsonSchema): string[] {
  return Array.isArray(s.required) ? s.required.filter((k): k is string => typeof k === 'string') : [];
}

export function itemsOf(s: JsonSchema, root: JsonSchema): JsonSchema {
  return isObj(s.items) ? resolve(s.items, root) : {};
}

/** "has_flag" -> "Has flag" (prefers the schema title's wording, sentence-cased). */
export function labelFor(key: string, s: JsonSchema): string {
  const raw = typeof s.title === 'string' && s.title ? s.title : key.replace(/_/g, ' ');
  const lower = raw.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}
