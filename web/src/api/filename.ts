// Every file the dashboard saves gets its name from here (server Content-Disposition, MIME
// attachment names, artifact refs): never a directory, never invisible or reordering characters.

// C0/C1 controls; bidi embeddings/overrides (U+202A-202E) and isolates (U+2066-2069); LRM/RLM
// (U+200E/200F); ALM (U+061C); zero-width space/joiners (U+200B-200D); BOM (U+FEFF); line and
// paragraph separators (U+2028/2029). Written as escapes so the source shows what is matched.
// eslint-disable-next-line no-control-regex
const UNSAFE = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2066-\u2069\u061c\ufeff]/g;

/** A safe base name for `name`, or null when nothing usable is left. */
export function safeBaseName(name: string | null | undefined): string | null {
  if (typeof name !== 'string') return null;
  const base = (name.replace(UNSAFE, '').split(/[\\/]/).pop() as string).trim();
  return base && base !== '.' && base !== '..' ? base : null;
}

/** The first candidate that is a safe name, else `fallback`. */
export function pickFilename(candidates: readonly (string | null | undefined)[], fallback = 'download'): string {
  for (const c of candidates) {
    const safe = safeBaseName(c);
    if (safe) return safe;
  }
  return fallback;
}
