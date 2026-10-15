import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

export const KEYS = {
  controller: 'mk_controller',
  controllerEmpty: 'mk_controller_empty',
  evaluator: 'mk_evaluator',
  agent: 'mk_agent',
} as const;

/** Fails on serious/critical axe violations (WCAG 2.x A/AA rules). */
export async function expectAccessible(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const summary = serious.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious, `${label}: ${summary.join('\n')}`).toEqual([]);
}
