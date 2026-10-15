// Shared YAML subset for the mock API (UX-F1 builder + UX-F2 answer-key editor): block
// maps/sequences, plain/quoted scalars, flow sequences of scalars, empty {} / [] and |/> block
// scalars. Used by /v1/tools/*, the scenario-draft and answer-key validators. Mock-only.
type Json = Record<string, unknown>;

// ---- YAML subset ----------------------------------------------------------------------------
const PLAIN = /^[A-Za-z_./@][A-Za-z0-9_ ./@+()-]*$/;
const RESERVED = /^(true|false|null|yes|no|on|off|~)$/i;

function scalar(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  const s = String(v);
  return PLAIN.test(s) && !RESERVED.test(s) && !s.endsWith(' ') && !s.includes(': ') && !s.includes(' #') ? s : JSON.stringify(s);
}

export const isMap = (v: unknown): v is Json => !!v && typeof v === 'object' && !Array.isArray(v);
const isEmpty = (v: unknown) => (Array.isArray(v) ? v.length === 0 : isMap(v) ? Object.keys(v).length === 0 : false);

export function dumpYaml(value: unknown, indent = 0): string {
  const pad = ' '.repeat(indent);
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (isMap(item) && !isEmpty(item)) {
          const inner = dumpYaml(item, indent + 2);
          return `${pad}- ${inner.slice(indent + 2)}`;
        }
        if (Array.isArray(item) && !isEmpty(item)) return `${pad}-\n${dumpYaml(item, indent + 2)}`;
        return `${pad}- ${isEmpty(item) ? (Array.isArray(item) ? '[]' : '{}') : scalar(item)}\n`;
      })
      .join('');
  }
  if (isMap(value)) {
    return Object.entries(value)
      .map(([k, v]) => {
        const key = scalar(k);
        if (isEmpty(v)) return `${pad}${key}: ${Array.isArray(v) ? '[]' : '{}'}\n`;
        if (isMap(v) || Array.isArray(v)) return `${pad}${key}:\n${dumpYaml(v, indent + 2)}`;
        return `${pad}${key}: ${scalar(v)}\n`;
      })
      .join('');
  }
  return `${pad}${scalar(value)}\n`;
}

export class YamlError extends Error {
  constructor(
    readonly line: number,
    message: string,
  ) {
    super(message);
  }
}

interface Line {
  no: number;
  indent: number;
  text: string;
}

function parseScalar(raw: string, no: number): unknown {
  const t = raw.trim();
  if (t === '' || t === '~' || t === 'null') return null;
  if (t === 'true') return true;
  if (t === 'false') return false;
  if (/^-?\d+$/.test(t)) return Number(t);
  if (/^-?\d+\.\d+$/.test(t)) return Number(t);
  if (t.startsWith('"')) {
    try {
      return JSON.parse(t) as unknown;
    } catch {
      throw new YamlError(no, 'unterminated or invalid double-quoted string');
    }
  }
  if (t.startsWith("'")) {
    if (!t.endsWith("'") || t.length < 2) throw new YamlError(no, 'unterminated single-quoted string');
    return t.slice(1, -1).replace(/''/g, "'");
  }
  if (t === '[]') return [];
  if (t === '{}') return {};
  if (t.startsWith('[')) {
    if (!t.endsWith(']')) throw new YamlError(no, 'unterminated flow sequence');
    const items = t.slice(1, -1).match(/"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^,]+/g) ?? [];
    return items.map((s) => s.trim()).filter((s) => s !== '').map((s) => parseScalar(s, no));
  }
  if (t.startsWith('{') || t.startsWith('&') || t.startsWith('*') || t.startsWith('!')) {
    throw new YamlError(no, 'unsupported YAML syntax in the mock');
  }
  return t;
}

function splitKey(text: string, no: number): [string, string] {
  const m = /^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^:#]+?)\s*:(?:\s+(.*)|$)/.exec(text);
  if (!m) throw new YamlError(no, `expected "key: value", found ${JSON.stringify(text)}`);
  return [String(parseScalar(m[1] as string, no)), m[2] ?? ''];
}

export function parseYaml(source: string): unknown {
  const lines: Line[] = [];
  source.split(/\r?\n/).forEach((raw, i) => {
    if (/^\s*(#.*)?$/.test(raw)) return;
    if (/^\s*\t/.test(raw)) throw new YamlError(i + 1, 'tabs are not allowed for indentation');
    const indent = raw.length - raw.trimStart().length;
    lines.push({ no: i + 1, indent, text: raw.trim().replace(/\s+#.*$/, (m) => (/["']/.test(m) ? m : '')) });
  });
  let pos = 0;

  const block = (indent: number, style: string): string => {
    const parts: string[] = [];
    while (pos < lines.length && (lines[pos] as Line).indent > indent) {
      parts.push((lines[pos] as Line).text);
      pos += 1;
    }
    return style.startsWith('|') ? `${parts.join('\n')}\n` : `${parts.join(' ')}\n`;
  };

  const value = (rest: string, line: Line, indent: number): unknown => {
    if (rest === '|' || rest === '>' || rest === '|-' || rest === '>-') {
      const text = block(indent, rest);
      return rest.endsWith('-') ? text.replace(/\n$/, '') : text;
    }
    if (rest !== '') return parseScalar(rest, line.no);
    const next = lines[pos];
    if (!next || next.indent < indent || (next.indent === indent && !next.text.startsWith('-'))) return null;
    return node(next.indent);
  };

  const seq = (indent: number): unknown[] => {
    const out: unknown[] = [];
    while (pos < lines.length) {
      const line = lines[pos] as Line;
      if (line.indent < indent) break;
      if (line.indent > indent) throw new YamlError(line.no, 'unexpected indentation');
      if (!line.text.startsWith('-')) break;
      const content = line.text.replace(/^-\s?/, '');
      if (content === '') {
        pos += 1;
        out.push(value('', line, indent + 1));
      } else if (/^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^:#"'[\]{}]+?)\s*:(\s|$)/.test(content)) {
        lines[pos] = { no: line.no, indent: indent + 2, text: content };
        out.push(map(indent + 2));
      } else {
        pos += 1;
        out.push(value(content, line, indent));
      }
    }
    return out;
  };

  const map = (indent: number): Json => {
    const out: Json = {};
    while (pos < lines.length) {
      const line = lines[pos] as Line;
      if (line.indent < indent) break;
      if (line.indent > indent) throw new YamlError(line.no, 'unexpected indentation');
      if (line.text.startsWith('- ') || line.text === '-') break;
      const [key, rest] = splitKey(line.text, line.no);
      if (key in out) throw new YamlError(line.no, `duplicate key ${JSON.stringify(key)}`);
      pos += 1;
      out[key] = value(rest, line, indent);
    }
    return out;
  };

  const node = (indent: number): unknown => {
    const line = lines[pos] as Line;
    return line.text.startsWith('-') ? seq(indent) : map(indent);
  };

  if (lines.length === 0) return null;
  const first = lines[0] as Line;
  const result = first.text.startsWith('-') || first.text.includes(':') ? node(first.indent) : parseScalar(first.text, first.no);
  if (pos < lines.length && result !== null && typeof result === 'object') {
    throw new YamlError((lines[pos] as Line).no, 'unexpected content (check the indentation)');
  }
  return result;
}

