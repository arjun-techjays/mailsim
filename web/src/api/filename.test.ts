import { pickFilename, safeBaseName } from './filename';

describe('safeBaseName', () => {
  it('removes bidi overrides/isolates and marks, so an extension cannot be disguised', () => {
    // "invoice\u202Efdp.exe" displays as "invoiceexe.pdf" in a right-to-left override
    expect(safeBaseName('invoice\u202Efdp.exe')).toBe('invoicefdp.exe');
    expect(safeBaseName('a\u202Ab\u202Bc\u202Cd\u202De\u2066f\u2067g\u2068h\u2069i\u200Ej\u200Fk\u061Cl.txt')).toBe('abcdefghijkl.txt');
  });

  it('removes C0/C1 controls and DEL, keeps ordinary Unicode', () => {
    expect(safeBaseName('a\u0000b\u0007c\u001fd\u007fe\u0085f\u009fg.pdf')).toBe('abcdefg.pdf');
    expect(safeBaseName('résumé 2026.pdf')).toBe('résumé 2026.pdf');
  });

  it('keeps only the base name and rejects dot names and empties', () => {
    expect(safeBaseName('../../etc/passwd')).toBe('passwd');
    expect(safeBaseName('C:\\x\\evil.exe')).toBe('evil.exe');
    expect(safeBaseName('blob/raw/msg_1.eml')).toBe('msg_1.eml');
    for (const bad of ['.', '..', '', '   ', 'dir/', '\u202E', null, undefined]) expect(safeBaseName(bad)).toBeNull();
  });
});

describe('pickFilename', () => {
  it('uses the first safe candidate, else the fallback', () => {
    expect(pickFilename([null, '..', 'ok.txt', 'later.txt'])).toBe('ok.txt');
    expect(pickFilename(['/'], 'attachment-1')).toBe('attachment-1');
    expect(pickFilename([])).toBe('download');
  });
});
