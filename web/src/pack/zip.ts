// Minimal, defensive .zip reader for scenario packs (no dependency). It reads the central
// directory only, refuses what a pack never needs (encryption, ZIP64, symlinks, unknown
// compression, unsafe or duplicate names) and inflates with the browser's DecompressionStream,
// stopping as soon as an entry grows past its declared size or the pack's budget (zip bombs).

export interface ZipLimits {
  /** Size of the .zip file itself. */
  maxZipBytes: number;
  /** Entries (files and folders) in the central directory. */
  maxEntries: number;
  /** Uncompressed bytes of one file. */
  maxEntryBytes: number;
  /** Uncompressed bytes of all files together. */
  maxTotalBytes: number;
  /** Uncompressed / compressed, for entries larger than 1 MB. */
  maxRatio: number;
}

export interface ZipEntry {
  path: string;
  data: Uint8Array;
}

export class PackError extends Error {}

const EOCD = 0x06054b50;
const CENTRAL = 0x02014b50;
const LOCAL = 0x04034b50;
const S_IFMT = 0o170000;
const S_IFLNK = 0o120000;

let crcTable: Uint32Array | null = null;
export function crc32(data: Uint8Array): number {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (const byte of data) crc = (crcTable[(crc ^ byte) & 0xff] as number) ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/**
 * Refused anywhere in a path: backslash, C0/DEL/C1 controls, zero-width characters and LRM/RLM,
 * bidi embeddings/overrides/isolates, line/paragraph separators and BOM (names that would
 * display differently from what they are).
 */
// eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
const UNSAFE_CHARS = /[\\\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]/;

/** A relative path with no empty, `.` or `..` segments, drive letters or unsafe characters. */
export function safeEntryPath(name: string): string | null {
  const path = name.replace(/^\.\//, '');
  if (!path || path.startsWith('/') || UNSAFE_CHARS.test(path) || /^[A-Za-z]:/.test(path)) return null;
  const parts = path.replace(/\/$/, '').split('/');
  return parts.some((p) => p === '' || p === '.' || p === '..') ? null : path;
}

async function inflate(raw: Uint8Array, expected: number, budget: number): Promise<Uint8Array> {
  const source = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(raw);
      controller.close();
    },
  });
  const stream = source.pipeThrough(new DecompressionStream('deflate-raw') as unknown as ReadableWritablePair<Uint8Array, Uint8Array>);
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > expected || size > budget) throw new PackError('a file in the .zip is larger than it claims (refused as a possible zip bomb)');
      chunks.push(value);
    }
  } catch (err) {
    void reader.cancel().catch(() => undefined);
    throw err instanceof PackError ? err : new PackError('a file in the .zip could not be decompressed');
  }
  const out = new Uint8Array(size);
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.length;
  }
  return out;
}

export async function readZip(bytes: Uint8Array, limits: ZipLimits): Promise<ZipEntry[]> {
  if (bytes.length > limits.maxZipBytes) throw new PackError(`the .zip is larger than ${limits.maxZipBytes} bytes`);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 22 - 0xffff); i -= 1) {
    if (view.getUint32(i, true) === EOCD) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new PackError('not a .zip file (no central directory)');
  const count = view.getUint16(eocd + 10, true);
  const cdSize = view.getUint32(eocd + 12, true);
  const cdStart = view.getUint32(eocd + 16, true);
  if (count === 0xffff || cdStart === 0xffffffff) throw new PackError('ZIP64 archives are not supported');
  if (count > limits.maxEntries) throw new PackError(`the .zip has ${count} entries (at most ${limits.maxEntries})`);
  if (cdStart + cdSize > eocd) throw new PackError('the .zip central directory is damaged');
  const decoder = new TextDecoder('utf-8', { fatal: false });
  const entries: ZipEntry[] = [];
  const seen = new Set<string>();
  let total = 0;
  let at = cdStart;
  for (let i = 0; i < count; i += 1) {
    if (at + 46 > eocd || view.getUint32(at, true) !== CENTRAL) throw new PackError('the .zip central directory is damaged');
    const flags = view.getUint16(at + 8, true);
    const method = view.getUint16(at + 10, true);
    const crc = view.getUint32(at + 16, true);
    const csize = view.getUint32(at + 20, true);
    const usize = view.getUint32(at + 24, true);
    const nameLen = view.getUint16(at + 28, true);
    const extraLen = view.getUint16(at + 30, true);
    const commentLen = view.getUint16(at + 32, true);
    const mode = view.getUint32(at + 38, true) >>> 16;
    const local = view.getUint32(at + 42, true);
    const rawName = decoder.decode(bytes.subarray(at + 46, at + 46 + nameLen));
    at += 46 + nameLen + extraLen + commentLen;
    const path = safeEntryPath(rawName);
    if (path === null) throw new PackError(`unsafe path in the .zip: ${JSON.stringify(rawName.slice(0, 100))}`);
    if (path.endsWith('/')) continue; // a folder
    if (seen.has(path)) throw new PackError(`the .zip lists ${path} twice`);
    seen.add(path);
    if ((mode & S_IFMT) === S_IFLNK) throw new PackError(`symbolic links are not allowed: ${path}`);
    if (flags & 1) throw new PackError(`encrypted files are not supported: ${path}`);
    if (csize === 0xffffffff || usize === 0xffffffff) throw new PackError('ZIP64 archives are not supported');
    if (method !== 0 && method !== 8) throw new PackError(`unsupported compression in ${path}`);
    if (usize > limits.maxEntryBytes) throw new PackError(`${path} is larger than ${limits.maxEntryBytes} bytes`);
    total += usize;
    if (total > limits.maxTotalBytes) throw new PackError(`the files in the .zip exceed ${limits.maxTotalBytes} bytes in total`);
    if (usize > 1024 * 1024 && usize > csize * limits.maxRatio) throw new PackError(`${path} is compressed suspiciously well (refused as a possible zip bomb)`);
    if (local + 30 > bytes.length || view.getUint32(local, true) !== LOCAL) throw new PackError(`the .zip entry ${path} is damaged`);
    const dataStart = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
    if (dataStart + csize > bytes.length) throw new PackError(`the .zip entry ${path} is damaged`);
    const raw = bytes.subarray(dataStart, dataStart + csize);
    if (method === 0 && csize !== usize) throw new PackError(`the .zip entry ${path} is damaged`);
    const data = method === 0 ? raw.slice() : await inflate(raw, usize, limits.maxTotalBytes - (total - usize));
    if (data.length !== usize || crc32(data) !== crc) throw new PackError(`the .zip entry ${path} is damaged (checksum mismatch)`);
    entries.push({ path, data });
  }
  return entries;
}
