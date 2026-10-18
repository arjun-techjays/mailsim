// Uploaded message files <-> the fixture paths a scenario already references (QA D-1).
// A pasted YAML lists `fixtures/<name>` for every initial message; uploading those files must
// fill exactly those references (by EXACT file name, never a sanitised one) and must never add
// a second copy of a message.
export interface UploadLike {
  name: string;
  /** Path inside an uploaded folder (`pack/fixtures/a.eml`), when known. */
  webkitRelativePath?: string;
}

export interface UploadPlan<F extends UploadLike> {
  /** Files that fill a referenced-but-missing fixture path. */
  fills: { path: string; file: F }[];
  /** Files no reference asked for. */
  unmatched: F[];
  /** Files left out, with the reason (shown to the user). */
  skipped: { name: string; reason: string }[];
}

export function baseName(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1);
}

/** A folder upload's path without the folder itself (`pack/fixtures/a.eml` -> `fixtures/a.eml`). */
function packPath(file: UploadLike): string | null {
  const rel = file.webkitRelativePath ?? '';
  const slash = rel.indexOf('/');
  return slash > 0 ? rel.slice(slash + 1) : null;
}

/**
 * Matches uploads against the fixture paths the draft references.
 * - `referenced`: every fixture path a mailbox lists (duplicates allowed);
 * - `present`: paths whose file content is already in the draft.
 * A file fills the reference with the same path (folder uploads) or, failing that, the one
 * reference whose last segment equals the file name exactly. Nothing is renamed.
 */
export function planUploads<F extends UploadLike>(files: readonly F[], referenced: readonly string[], present: ReadonlySet<string>): UploadPlan<F> {
  const refs = [...new Set(referenced)];
  const plan: UploadPlan<F> = { fills: [], unmatched: [], skipped: [] };
  const taken = new Set<string>();
  for (const file of files) {
    const exact = packPath(file);
    let candidates = exact !== null && refs.includes(exact) ? [exact] : refs.filter((r) => baseName(r) === file.name);
    if (candidates.length === 0) {
      plan.unmatched.push(file);
      continue;
    }
    const open = candidates.filter((p) => !present.has(p));
    if (open.length === 0) {
      plan.skipped.push({ name: file.name, reason: `already in the scenario as ${candidates.join(', ')}` });
      continue;
    }
    candidates = open;
    if (candidates.length > 1) {
      plan.skipped.push({ name: file.name, reason: `matches several messages (${candidates.join(', ')}): upload the folder to match by path` });
      continue;
    }
    const path = candidates[0] as string;
    if (taken.has(path)) {
      plan.skipped.push({ name: file.name, reason: `uploaded twice for ${path}` });
      continue;
    }
    taken.add(path);
    plan.fills.push({ path, file });
  }
  return plan;
}
