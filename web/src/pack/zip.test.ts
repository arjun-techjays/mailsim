import { deflateRaw, makeZip, type FakeEntry } from '../test/zip';
import { crc32, PackError, readZip, safeEntryPath, type ZipLimits } from './zip';

const LIMITS: ZipLimits = { maxZipBytes: 64 * 1024, maxEntries: 10, maxEntryBytes: 4 * 1024 * 1024, maxTotalBytes: 8 * 1024 * 1024, maxRatio: 100 };
const text = (d: Uint8Array) => new TextDecoder().decode(d);
const refused = async (entries: FakeEntry[], message: RegExp, opts = {}, limits = LIMITS) => {
  const zip = await makeZip(entries, opts);
  await expect(readZip(zip, limits)).rejects.toThrow(message);
  await expect(readZip(zip, limits)).rejects.toBeInstanceOf(PackError);
};

describe('readZip', () => {
  it('reads stored and deflated files, skips folders and keeps names exactly', async () => {
    const zip = await makeZip(
      [{ name: 'cbo/' }, { name: './cbo/scenario.yaml', data: 'a: 1\n' }, { name: 'cbo/fixtures/003-deal-.eml', data: 'From: x@y.test\r\n\r\nhi'.repeat(50), deflate: true }],
      { trailing: 0 },
    );
    const entries = await readZip(zip, LIMITS);
    expect(entries.map((e) => e.path)).toEqual(['cbo/scenario.yaml', 'cbo/fixtures/003-deal-.eml']);
    expect(text(entries[0]!.data)).toBe('a: 1\n');
    expect(text(entries[1]!.data)).toContain('From: x@y.test');
  });

  it('finds the central directory behind a comment-sized tail', async () => {
    const zip = await makeZip([{ name: 'a.eml', data: 'x' }]);
    const padded = new Uint8Array([...zip, ...new Uint8Array(10)]);
    expect((await readZip(padded, LIMITS)).map((e) => e.path)).toEqual(['a.eml']);
  });

  it('refuses archives that are too large, not zips, ZIP64 or damaged', async () => {
    await expect(readZip(new Uint8Array(LIMITS.maxZipBytes + 1), LIMITS)).rejects.toThrow(/larger than/);
    await expect(readZip(new Uint8Array(30), LIMITS)).rejects.toThrow(/not a .zip file/);
    await expect(readZip(new Uint8Array(3), LIMITS)).rejects.toThrow(/not a .zip file/);
    await refused([{ name: 'a' }], /ZIP64/, { count: 0xffff });
    await refused([{ name: 'a' }], /ZIP64/, { cdOffset: 0xffffffff });
    await refused([{ name: 'a' }], /has 11 entries/, { count: 11 });
    await refused([{ name: 'a' }], /central directory is damaged/, { cdSize: 9999 });
    await refused([{ name: 'a' }], /central directory is damaged/, { count: 2 });
    await refused([{ name: 'a', data: 'x' }], /central directory is damaged/, { cdOffset: 0 });
  });

  it('refuses unsafe, duplicate, linked, encrypted or oddly compressed entries', async () => {
    for (const name of ['../x.eml', '/etc/passwd', 'a/../../b', 'C:evil', 'a\\b', 'a\u0000b', 'a//b', '', 'invoice\u202egpj.eml']) {
      await refused([{ name, data: 'x' }], /unsafe path/);
    }
    await refused([{ name: 'a.eml', data: 'x' }, { name: 'a.eml', data: 'y' }], /lists a.eml twice/);
    await refused([{ name: 'link.eml', data: '/etc/passwd', mode: 0o120777 }], /symbolic links/);
    await refused([{ name: 'a.eml', data: 'x', flags: 1 }], /encrypted/);
    await refused([{ name: 'a.eml', data: 'x', method: 12 }], /unsupported compression/);
    await refused([{ name: 'a.eml', data: 'x', usize: 0xffffffff }], /ZIP64/);
    await refused([{ name: 'a.eml', data: 'x', csize: 0xffffffff }], /ZIP64/);
  });

  it('enforces size limits before inflating and stops zip bombs while inflating', async () => {
    await refused([{ name: 'a.eml', data: 'x', usize: LIMITS.maxEntryBytes + 1 }], /a.eml is larger than/);
    await refused([{ name: 'a', data: 'x' }, { name: 'b', data: 'y' }], /exceed 1 bytes in total/, {}, { ...LIMITS, maxTotalBytes: 1 });
    await refused([{ name: 'bomb.eml', data: 'x', usize: 2 * 1024 * 1024 }], /compressed suspiciously well/);
    // the header claims 10 bytes; the stream inflates to far more: stopped
    const zeros = new Uint8Array(200_000);
    await refused([{ name: 'liar.eml', raw: await deflateRaw(zeros), method: 8, usize: 10, crc: crc32(zeros) }], /larger than it claims/);
    await refused([{ name: 'junk.eml', raw: new Uint8Array([0xff, 0xff, 0xff]), method: 8, usize: 3 }], /could not be decompressed/);
  });

  it('refuses damaged entries (local header, truncated data, size or checksum mismatch)', async () => {
    await refused([{ name: 'a.eml', data: 'x', localOffset: 5 }], /entry a.eml is damaged/);
    await refused([{ name: 'a.eml', data: 'x', localOffset: 60_000 }], /entry a.eml is damaged/);
    await refused([{ name: 'a.eml', data: 'x', csize: 5000, usize: 5000 }], /entry a.eml is damaged/);
    await refused([{ name: 'a.eml', data: 'xy', usize: 1 }], /entry a.eml is damaged/);
    await refused([{ name: 'a.eml', data: 'x', crc: 1 }], /checksum mismatch/);
    await refused([{ name: 'a.eml', data: 'hello', deflate: true, usize: 6 }], /checksum mismatch/);
  });
});

describe('safeEntryPath and crc32', () => {
  it('refuses C1 controls, bidi, zero-width, separators and BOM (display spoofing)', () => {
    const spoofers = ['\u0080', '\u009f', '\u200b', '\u200c', '\u200d', '\u200e', '\u200f', '\u202a', '\u202e', '\u2066', '\u2069', '\u2028', '\u2029', '\ufeff', '\u061c', '\u180e', '\u2060', '\u2062', '\u2064', '\u007f', '\u001f'];
    for (const ch of spoofers) expect(safeEntryPath(`fixtures/a${ch}b.eml`), JSON.stringify(ch)).toBeNull();
    expect(safeEntryPath('fixtures/a\u00a0b\u2030.eml')).toBe('fixtures/a\u00a0b\u2030.eml'); // just outside the ranges
  });

  it('normalises a leading ./ and keeps everything else', () => {
    expect(safeEntryPath('./a/b-.eml')).toBe('a/b-.eml');
    expect(safeEntryPath('a/')).toBe('a/');
    expect(safeEntryPath('a b/ü.eml')).toBe('a b/ü.eml');
    expect(safeEntryPath('a/./b')).toBeNull();
  });
  it('matches the standard CRC-32', () => {
    expect(crc32(new TextEncoder().encode('123456789'))).toBe(0xcbf43926);
    expect(crc32(new Uint8Array())).toBe(0);
  });
});
