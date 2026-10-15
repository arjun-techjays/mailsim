import type { Address } from '../api/types';

export function pct(v: number | null | undefined): string {
  return v == null ? 'n/a' : `${(v * 100).toFixed(1)}%`;
}

/** Signed percentage-point delta, e.g. +19.0 pp. */
export function delta(v: number | null | undefined): string {
  if (v == null) return 'n/a';
  const pp = (v * 100).toFixed(1);
  return `${v > 0 ? '+' : ''}${pp} pp`;
}

export function num(v: number | null | undefined, digits = 3): string {
  return v == null ? 'n/a' : String(Number(v.toFixed(digits)));
}

export function bytes(n: number | null | undefined): string {
  if (n == null) return 'unknown';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function address(a: Address | null | undefined): string {
  if (!a) return '';
  return a.name ? `${a.name} <${a.email}>` : a.email;
}

export function addresses(list: Address[] | null | undefined): string {
  return (list ?? []).map(address).join(', ');
}

export function splitList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}
