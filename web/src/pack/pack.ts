// A scenario pack: scenario.yaml (or .yml/.json), its .eml fixtures and an optional answer key,
// as a folder or a .zip. Only those file types are read and sent; everything else is listed as
// ignored. Fixture paths are the files' paths relative to the scenario file, unchanged.
import { bytesToBase64, fileBytes } from '../builder/files';
import { PackError, readZip, safeEntryPath, type ZipEntry, type ZipLimits } from './zip';

/** Same limits as the API: 5 MB per message file, 200 files, 25 MB of files per scenario. */
export const PACK_LIMITS: ZipLimits & { maxFixtures: number; maxDocumentBytes: number } = {
  maxZipBytes: 30 * 1024 * 1024,
  maxEntries: 400,
  maxEntryBytes: 5 * 1024 * 1024,
  maxTotalBytes: 25 * 1024 * 1024,
  maxRatio: 100,
  maxFixtures: 200,
  maxDocumentBytes: 1024 * 1024,
};

const SCENARIO = /^scenario\.(ya?ml|json)$/;
const ANSWER_KEY = /^answer_key\.(ya?ml|json)$/;

export interface Pack {
  /** Folder of the scenario file inside the upload ('' at the top). */
  root: string;
  scenarioFile: string;
  source: string;
  answerKeyFile: string | null;
  answerKeySource: string | null;
  /** Fixture path (relative to the scenario file) -> base64. */
  fixtures: Record<string, string>;
  /** Files that were not read (not a pack file type, or outside the scenario's folder). */
  ignored: string[];
}

const leaf = (p: string) => p.slice(p.lastIndexOf('/') + 1);
const folder = (p: string) => p.slice(0, p.lastIndexOf('/') + 1);
/** Finder / Explorer litter and macOS resource forks: skipped without a mention. */
const litter = (p: string) => p.split('/').some((s) => (s.startsWith('.') && s !== '.' && s !== '..') || s === '__MACOSX' || s === 'Thumbs.db');

function text(entry: ZipEntry, what: string): string {
  if (entry.data.length > PACK_LIMITS.maxDocumentBytes) throw new PackError(`${what} ${entry.path} is larger than 1 MB`);
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(entry.data);
  } catch {
    throw new PackError(`${what} ${entry.path} is not UTF-8 text`);
  }
}

export function buildPack(entries: ZipEntry[]): Pack {
  const files = entries.filter((e) => !litter(e.path));
  const scenarios = files.filter((e) => SCENARIO.test(leaf(e.path)));
  if (scenarios.length === 0) throw new PackError('no scenario.yaml (or scenario.json) in the pack');
  if (scenarios.length > 1) {
    throw new PackError(`the pack has several scenario files (${scenarios.map((e) => e.path).join(', ')}): import one scenario at a time`);
  }
  const scenario = scenarios[0] as ZipEntry;
  const root = folder(scenario.path);
  const keys = files.filter((e) => folder(e.path) === root && ANSWER_KEY.test(leaf(e.path)));
  if (keys.length > 1) throw new PackError(`the pack has several answer keys (${keys.map((e) => e.path).join(', ')})`);
  const key = keys[0] ?? null;
  const fixtures: Record<string, string> = {};
  const ignored: string[] = [];
  for (const e of files) {
    if (e === scenario || e === key) continue;
    if (e.path.startsWith(root) && /\.eml$/i.test(e.path)) {
      if (Object.keys(fixtures).length >= PACK_LIMITS.maxFixtures) throw new PackError(`the pack has more than ${PACK_LIMITS.maxFixtures} .eml files`);
      fixtures[e.path.slice(root.length)] = bytesToBase64(e.data);
    } else {
      ignored.push(e.path);
    }
  }
  return {
    root,
    scenarioFile: scenario.path,
    source: text(scenario, 'the scenario'),
    answerKeyFile: key?.path ?? null,
    answerKeySource: key ? text(key, 'the answer key') : null,
    fixtures,
    ignored,
  };
}

export function readZipPack(bytes: Uint8Array): Promise<ZipEntry[]> {
  return readZip(bytes, PACK_LIMITS);
}

/** A folder picked with <input webkitdirectory>: the same limits as a .zip, checked before reading. */
export async function readFolderPack(files: readonly File[]): Promise<ZipEntry[]> {
  const wanted = files.filter((f) => {
    const name = f.webkitRelativePath || f.name;
    return !litter(name) && (SCENARIO.test(leaf(name)) || ANSWER_KEY.test(leaf(name)) || /\.eml$/i.test(name));
  });
  const skipped = files.filter((f) => !wanted.includes(f) && !litter(f.webkitRelativePath || f.name));
  if (files.length > PACK_LIMITS.maxEntries) throw new PackError(`the folder has ${files.length} files (at most ${PACK_LIMITS.maxEntries})`);
  let total = 0;
  const out: ZipEntry[] = skipped.map((f) => ({ path: f.webkitRelativePath || f.name, data: new Uint8Array() }));
  for (const f of wanted) {
    const path = safeEntryPath(f.webkitRelativePath || f.name);
    if (path === null) throw new PackError(`unsafe path in the folder: ${JSON.stringify(f.webkitRelativePath)}`);
    if (f.size > PACK_LIMITS.maxEntryBytes) throw new PackError(`${path} is larger than ${PACK_LIMITS.maxEntryBytes} bytes`);
    total += f.size;
    if (total > PACK_LIMITS.maxTotalBytes) throw new PackError(`the files in the folder exceed ${PACK_LIMITS.maxTotalBytes} bytes in total`);
    out.push({ path, data: await fileBytes(f) });
  }
  return out;
}
