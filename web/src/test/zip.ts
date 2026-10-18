// Test-only .zip writer (stored or deflated entries), with knobs to forge broken archives.
import { crc32 } from '../pack/zip';

export interface FakeEntry {
  name: string;
  data?: Uint8Array | string;
  deflate?: boolean;
  /** Overrides of central-directory fields (to forge damaged or hostile archives). */
  method?: number;
  flags?: number;
  crc?: number;
  usize?: number;
  csize?: number;
  mode?: number;
  localOffset?: number;
  /** Replaces the compressed bytes. */
  raw?: Uint8Array;
}

const enc = new TextEncoder();

export async function deflateRaw(data: Uint8Array): Promise<Uint8Array> {
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(data);
      c.close();
    },
  }).pipeThrough(new CompressionStream('deflate-raw') as unknown as ReadableWritablePair<Uint8Array, Uint8Array>);
  const chunks: number[] = [];
  const reader = stream.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return new Uint8Array(chunks);
    chunks.push(...value);
  }
}

export async function makeZip(entries: FakeEntry[], opts: { count?: number; cdOffset?: number; cdSize?: number; trailing?: number } = {}): Promise<Uint8Array> {
  const locals: number[] = [];
  const centrals: number[] = [];
  let offset = 0;
  for (const e of entries) {
    const data = typeof e.data === 'string' ? enc.encode(e.data) : (e.data ?? new Uint8Array());
    const packed = e.raw ?? (e.deflate ? await deflateRaw(data) : data);
    const name = enc.encode(e.name);
    const method = e.method ?? (e.deflate ? 8 : 0);
    const crc = e.crc ?? crc32(data);
    const usize = e.usize ?? data.length;
    const csize = e.csize ?? packed.length;
    const local = new Uint8Array(30 + name.length + packed.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(8, method, true);
    lv.setUint32(14, crc, true);
    lv.setUint32(18, csize, true);
    lv.setUint32(22, usize, true);
    lv.setUint16(26, name.length, true);
    local.set(name, 30);
    local.set(packed, 30 + name.length);
    const central = new Uint8Array(46 + name.length);
    const cv = new DataView(central.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(8, e.flags ?? 0, true);
    cv.setUint16(10, method, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, csize, true);
    cv.setUint32(24, usize, true);
    cv.setUint16(28, name.length, true);
    cv.setUint32(38, ((e.mode ?? 0o100644) << 16) >>> 0, true);
    cv.setUint32(42, e.localOffset ?? offset, true);
    central.set(name, 46);
    locals.push(...local);
    centrals.push(...central);
    offset += local.length;
  }
  const eocd = new Uint8Array(22 + (opts.trailing ?? 0));
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(10, opts.count ?? entries.length, true);
  ev.setUint32(12, opts.cdSize ?? centrals.length, true);
  ev.setUint32(16, opts.cdOffset ?? offset, true);
  return new Uint8Array([...locals, ...centrals, ...eocd]);
}
