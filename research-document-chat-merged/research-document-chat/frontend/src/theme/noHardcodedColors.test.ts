import { describe, expect, it } from 'vitest';

/**
 * Colours are defined once, in src/index.css (CSS variables) and mapped in tailwind.config.js.
 * Components must use token classes (bg-card, text-fg, border-line, bg-accent, ...). This test fails when a
 * hex / rgb / hsl value or a Tailwind palette colour class (slate, red, teal, emerald, ...) appears anywhere else.
 */
const files = import.meta.glob('/src/**/*.{ts,tsx,css}', { query: '?raw', import: 'default', eager: true }) as Record<
  string,
  string
>;

/** Explicit, minimal allow-list (paths from the project root). */
const ALLOWED_FILES = new Set<string>(['/src/index.css']);

const PALETTE =
  'slate|gray|zinc|neutral|stone|red|rose|pink|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia';
const UTIL = 'bg|text|border(?:-[trblxy])?|ring|from|to|via|divide(?:-[xy])?|placeholder|fill|stroke|outline|accent|decoration|caret|shadow';

const PATTERNS: Array<{ name: string; re: RegExp }> = [
  { name: 'hex colour', re: /(?<![\w&/])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b(?![-\w])/ },
  { name: 'rgb/hsl colour', re: /\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\(/ },
  { name: 'palette class', re: new RegExp(`(?<![\\w-])(?:${UTIL})-(?:white|black|${PALETTE})(?:-\\d{2,3})?(?![\\w-])`) },
];

export function findHardcodedColours(): string[] {
  const hits: string[] = [];
  for (const [path, source] of Object.entries(files)) {
    if (ALLOWED_FILES.has(path) || path.startsWith('/src/theme/') || /\.test\.tsx?$/.test(path) || path.endsWith('.d.ts')) continue;
    source.split('\n').forEach((line: string, index: number) => {
      for (const { name, re } of PATTERNS) {
        const match = re.exec(line);
        if (match) hits.push(`${path.replace('/src/', '')}:${index + 1}  ${name}: ${match[0]}`);
      }
    });
  }
  return hits;
}

describe('design tokens', () => {
  it('scans the source tree', () => {
    expect(Object.keys(files).length).toBeGreaterThan(20);
  });

  it('has no hard-coded colours outside the token file', () => {
    const hits = findHardcodedColours();
    expect(hits, hits.join('\n')).toEqual([]);
  });

  it('keeps the allow-list minimal', () => {
    expect([...ALLOWED_FILES]).toEqual(['/src/index.css']);
  });
});
