// Tiny YAML subset for the mock /v1/tools endpoints (the real server uses PyYAML safe_load /
// safe_dump). Emits block YAML with JSON-quoted strings; parses that output back, plus flow
// collections ([a, b], {k: v}), comments and plain/quoted scalars. Mock-only: not a YAML parser.

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

const PLAIN_KEY = /^[A-Za-z_][\w.-]*$/;

function scalar(v: Json): string | null {
  if (v === null) return 'null';
  if (typeof v === 'boolean' || typeof v === 'number') return String(v);
  if (typeof v === 'string') return JSON.stringify(v);
  if (Array.isArray(v)) return v.length === 0 ? '[]' : null;
  return Object.keys(v).length === 0 ? '{}' : null;
}

function emitLines(v: Json, indent: number): string[] {
  const pad = ' '.repeat(indent);
  const out: string[] = [];
  if (Array.isArray(v)) {
    for (const item of v) {
      const s = scalar(item);
      if (s !== null) {
        out.push(`${pad}- ${s}`);
        continue;
      }
      const nested = emitLines(item, indent + 2);
      out.push(`${pad}- ${(nested[0] as string).trimStart()}`, ...nested.slice(1));
    }
    return out;
  }
  for (const [k, val] of Object.entries(v as Record<string, Json>)) {
    const key = PLAIN_KEY.test(k) ? k : JSON.stringify(k);
    const s = scalar(val);
    if (s !== null) out.push(`${pad}${key}: ${s}`);
    else out.push(`${pad}${key}:`, ...emitLines(val, indent + 2));
  }
  return out;
}

export function toYaml(doc: Record<string, unknown>): string {
  return `${emitLines(doc as Json, 0).join('\n')}\n`;
}

export class YamlError extends Error {
  constructor(
    message: string,
    readonly line: number,
  ) {
    super(message);
  }
}

function parsePlain(raw: string, line: number): Json {
  const s = raw.trim();
  if (s === '' || s === '~' || s === 'null') return null;
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s);
  if (s.startsWith('"')) {
    try {
      return JSON.parse(s) as string;
    } catch {
      throw new YamlError('unterminated double-quoted string', line);
    }
  }
  if (s.startsWith("'")) {
    if (!s.endsWith("'") || s.length < 2) throw new YamlError('unterminated single-quoted string', line);
    return s.slice(1, -1).replace(/''/g, "'");
  }
  if (s.startsWith('[') || s.startsWith('{')) return parseFlow(s, line);
  return s;
}

function parseFlow(src: string, line: number): Json {
  let i = 0;
  const ws = () => {
    while (src[i] === ' ') i += 1;
  };
  const token = (): string => {
    ws();
    if (src[i] === '"') {
      const start = i;
      i += 1;
      while (i < src.length && src[i] !== '"') i += src[i] === '\\' ? 2 : 1;
      i += 1;
      return src.slice(start, i);
    }
    const start = i;
    while (i < src.length && !',:]}'.includes(src[i] as string)) i += 1;
    return src.slice(start, i);
  };
  const value = (): Json => {
    ws();
    if (src[i] === '[') {
      i += 1;
      const arr: Json[] = [];
      ws();
      if (src[i] === ']') {
        i += 1;
        return arr;
      }
      for (;;) {
        arr.push(value());
        ws();
        if (src[i] === ',') i += 1;
        else if (src[i] === ']') {
          i += 1;
          return arr;
        } else throw new YamlError('expected , or ] in flow sequence', line);
      }
    }
    if (src[i] === '{') {
      i += 1;
      const obj: Record<string, Json> = {};
      ws();
      if (src[i] === '}') {
        i += 1;
        return obj;
      }
      for (;;) {
        const k = parsePlain(token(), line);
        ws();
        if (src[i] !== ':') throw new YamlError('expected : in flow mapping', line);
        i += 1;
        obj[String(k)] = value();
        ws();
        if (src[i] === ',') i += 1;
        else if (src[i] === '}') {
          i += 1;
          return obj;
        } else throw new YamlError('expected , or } in flow mapping', line);
      }
    }
    return parsePlain(token(), line);
  };
  const v = value();
  ws();
  if (i < src.length) throw new YamlError('unexpected text after flow collection', line);
  return v;
}

interface Line {
  indent: number;
  text: string;
  no: number;
}

function stripComment(s: string): string {
  let quote: string | null = null;
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i] as string;
    if (quote) {
      if (c === quote && s[i - 1] !== '\\') quote = null;
    } else if (c === '"' || c === "'") quote = c;
    else if (c === '#' && (i === 0 || s[i - 1] === ' ')) return s.slice(0, i);
  }
  return s;
}

function splitKey(text: string): [string, string] | null {
  let quote: string | null = null;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i] as string;
    if (quote) {
      if (c === quote && text[i - 1] !== '\\') quote = null;
    } else if (c === '"' || c === "'") quote = c;
    else if (c === '[' || c === '{') return null;
    else if (c === ':' && (i === text.length - 1 || text[i + 1] === ' ')) {
      return [text.slice(0, i).trim(), text.slice(i + 1).trim()];
    }
  }
  return null;
}

export function fromYaml(source: string): Json {
  const lines: Line[] = [];
  source.split('\n').forEach((raw, idx) => {
    if (raw.includes('\t')) throw new YamlError('tabs are not allowed for indentation', idx + 1);
    const text = stripComment(raw).trimEnd();
    if (text.trim() === '' || text.trim() === '---') return;
    lines.push({ indent: text.length - text.trimStart().length, text: text.trim(), no: idx + 1 });
  });
  let pos = 0;

  const block = (indent: number): Json => {
    const first = lines[pos];
    if (!first || first.indent < indent) return null;
    if (first.text === '-' || first.text.startsWith('- ')) return seq(first.indent);
    if (splitKey(first.text)) return map(first.indent);
    pos += 1;
    return parsePlain(first.text, first.no);
  };

  const map = (indent: number): Json => {
    const obj: Record<string, Json> = {};
    while (pos < lines.length) {
      const l = lines[pos] as Line;
      if (l.indent < indent) break;
      if (l.indent > indent) throw new YamlError('bad indentation', l.no);
      const kv = splitKey(l.text);
      if (!kv) throw new YamlError(`expected "key: value", got ${JSON.stringify(l.text)}`, l.no);
      pos += 1;
      const key = String(parsePlain(kv[0], l.no));
      obj[key] = kv[1] === '' ? block(indent + 1) : parsePlain(kv[1], l.no);
    }
    return obj;
  };

  const seq = (indent: number): Json => {
    const arr: Json[] = [];
    while (pos < lines.length) {
      const l = lines[pos] as Line;
      if (l.indent < indent || !(l.text === '-' || l.text.startsWith('- '))) {
        if (l.indent > indent) throw new YamlError('bad indentation', l.no);
        break;
      }
      const rest = l.text.slice(1).trimStart();
      if (rest === '') {
        pos += 1;
        arr.push(block(indent + 1));
      } else if (splitKey(rest)) {
        // "- key: value" starts a mapping whose keys align with `key`.
        lines[pos] = { indent: indent + 2, text: rest, no: l.no };
        arr.push(map(indent + 2));
      } else {
        pos += 1;
        arr.push(parsePlain(rest, l.no));
      }
    }
    return arr;
  };

  const out = block(0);
  if (pos < lines.length) throw new YamlError('unexpected content', (lines[pos] as Line).no);
  return out;
}
