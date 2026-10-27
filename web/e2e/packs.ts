// Scenario packs for the import / fixture-matching e2e tests (QA D-1): YAML, .eml files with
// tricky names (trailing hyphen, underscores, capitals) and a .zip writer (stored + deflate).
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { crc32, deflateRawSync } from 'node:zlib';

/** Fixture files: exact names, as a generator would write them (`003-deal-.eml` ends in `-`). */
export const TRICKY = ['fixtures/001-hello.eml', 'fixtures/003-the-expected-volume-on-the-deal-.eml', 'fixtures/014-fw-draft-with-questions-.eml', 'fixtures/Weird_Name-.eml'];

export function eml(id: string, subject: string, to = 'finance@example.test'): string {
  return [
    'From: Vendor <vendor@example.test>',
    `To: ${to}`,
    `Subject: ${subject}`,
    'Date: Thu, 15 Oct 2026 08:00:00 +0000',
    `Message-ID: <${id}@example.test>`,
    'MIME-Version: 1.0',
    'Content-Type: text/plain; charset=utf-8',
    '',
    `Body of ${subject}.`,
    '',
  ].join('\r\n');
}

/** A two-persona scenario: `fixtures` in the agent's (finance) mailbox, `vendorFixtures` in the vendor's. */
export function scenarioYaml(id: string, fixtures: string[], vendorFixtures: string[] = []): string {
  const list = (items: string[]) => (items.length ? ['      initial_messages:', ...items.map((f) => `        - fixture: ${f}`)] : []);
  return [
    'schema_version: "1.1"',
    'scenario:',
    `  id: ${id}`,
    '  version: 1',
    '  title: Pack import e2e',
    '  environment:',
    '    seed: 1',
    '    clock:',
    '      start_at: "2026-10-15T09:00:00Z"',
    '  organization:',
    '    domain: example.test',
    '  personas:',
    '    - id: finance',
    '      email: finance@example.test',
    '      role: Finance manager',
    '    - id: vendor',
    '      email: vendor@example.test',
    '      role: Vendor',
    '  mailboxes:',
    '    - owner: finance',
    ...list(fixtures),
    '    - owner: vendor',
    ...list(vendorFixtures),
    '  agent:',
    '    mailbox: finance',
    '',
  ].join('\n');
}

export function answerKeyYaml(id: string): string {
  return [
    'answer_key_version: "1.0"',
    'answer_key:',
    `  scenario: ${id}`,
    '  version: 1',
    '  assertions:',
    '    - id: nothing-sent-outside',
    '      type: state',
    '      requirement: Nothing is sent to the vendor',
    '      check:',
    '        where:',
    '          folder: sent',
    '          to: vendor@example.test',
    '        count:',
    '          eq: 0',
    '',
  ].join('\n');
}

export type PackFiles = Record<string, string | Buffer>;

/** Writes the pack under a fresh temp folder; returns the pack folder (what a user picks). */
export function writePack(name: string, files: PackFiles): string {
  const root = join(mkdtempSync(join(tmpdir(), 'mailsim-pack-')), name);
  for (const [rel, body] of Object.entries(files)) {
    mkdirSync(dirname(join(root, rel)), { recursive: true });
    writeFileSync(join(root, rel), body);
  }
  return root;
}

/** A .zip of `files` under folder `name/` (deflated). */
export function zipPack(name: string, files: PackFiles): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const [rel, body] of Object.entries(files)) {
    const data = Buffer.isBuffer(body) ? body : Buffer.from(body);
    const packed = deflateRawSync(data);
    const fname = Buffer.from(`${name}/${rel}`);
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(8, 8);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(packed.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(fname.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(8, 10);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(packed.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(fname.length, 28);
    central.writeUInt32LE(offset, 42);
    locals.push(local, fname, packed);
    centrals.push(central, fname);
    offset += local.length + fname.length + packed.length;
  }
  const cd = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(centrals.length / 2, 8);
  end.writeUInt16LE(centrals.length / 2, 10);
  end.writeUInt32LE(cd.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, end]);
}
