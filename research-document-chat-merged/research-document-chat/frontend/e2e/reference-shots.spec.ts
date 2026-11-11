/**
 * Regenerates the reference screenshots from docs/ui-reference (shell.html, kit.html) into docs/ui-baseline/reference/.
 * Opened from disk; the Google Fonts link may fail offline, which is fine (system sans fallback).
 */
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { test } from '@playwright/test';

const REF_DIR = resolve(process.cwd(), '..', 'docs', 'ui-reference');
const OUT = resolve(process.cwd(), '..', 'docs', 'ui-baseline', 'reference');
mkdirSync(OUT, { recursive: true });

const url = (file: string, query = '') => `${pathToFileURL(resolve(REF_DIR, file)).href}${query}`;

const SHOTS: Array<{ name: string; file: string; query: string; w: number; h: number; scheme: 'light' | 'dark' }> = [
  { name: 'ref-shell-light', file: 'shell.html', query: '?theme=light', w: 1440, h: 900, scheme: 'light' },
  { name: 'ref-shell-dark', file: 'shell.html', query: '?theme=dark', w: 1440, h: 900, scheme: 'dark' },
  { name: 'ref-kit-light', file: 'kit.html', query: '?theme=light', w: 1440, h: 900, scheme: 'light' },
  { name: 'ref-kit-dark', file: 'kit.html', query: '?theme=dark', w: 1440, h: 900, scheme: 'dark' },
  { name: 'ref-shell-mobile-light', file: 'shell.html', query: '?theme=light', w: 390, h: 844, scheme: 'light' },
];

for (const s of SHOTS) {
  test(`reference ${s.name}`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: s.w, height: s.h }, colorScheme: s.scheme });
    const page = await context.newPage();
    await page.goto(url(s.file, s.query), { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(600);
    await page.screenshot({ path: resolve(OUT, `${s.name}.png`), fullPage: s.file === 'kit.html' });
    await context.close();
  });
}
