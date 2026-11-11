#!/usr/bin/env node
/**
 * ui-lint: no hard-coded colours, palette classes, inline colour styles or !important outside the token files.
 *
 *   node scripts/ui-lint.mjs            # prints violations, exits 1 if any
 *   node scripts/ui-lint.mjs --summary  # counts only
 *
 * Rules (checked in frontend/src/**.{ts,tsx,css}, test files excluded):
 *   hex      #rgb / #rrggbb / #rrggbbaa
 *   fn       rgb( rgba( hsl( hsla( oklch( ...
 *   palette  Tailwind colour classes: bg-red-500, text-slate-700, border-white, from-cyan-400, ...
 *   inline   style={{ color|background|border...: ... }}
 *   important  "!important" in css/ts, and Tailwind important-prefixed classes such as !min-h-[42px]
 *
 * Allowed: the token blocks (:root and .dark) of src/index.css and anything listed in ui-lint-allow.json.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(fileURLToPath(new URL('.', import.meta.url)), '..');
const srcDir = join(root, 'src');
const allow = JSON.parse(readFileSync(join(root, 'ui-lint-allow.json'), 'utf8'));

const PALETTE =
  'slate|gray|zinc|neutral|stone|red|rose|pink|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia';
const UTIL = 'bg|text|border(?:-[trblxy])?|ring|from|to|via|divide(?:-[xy])?|placeholder|fill|stroke|outline|accent|decoration|caret|shadow';

const RULES = [
  { id: 'hex', re: /(?<![\w&/])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b(?![-\w])/g },
  { id: 'fn', re: /\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\(/g },
  { id: 'palette', re: new RegExp(`(?<![\\w-])(?:${UTIL})-(?:white|black|${PALETTE})(?:-\\d{2,3})?(?![\\w-])`, 'g') },
  { id: 'inline', re: /style=\{\{[^}]*\b(?:color|background(?:Color|Image)?|borderColor|border|fill|stroke|boxShadow)\s*:/g },
  { id: 'important', re: /!important/g },
];
// Tailwind "!" modifier inside a class string, e.g. "!min-h-[42px]"
const BANG_CLASS = /(?<=[\s"'`:])!(?=[a-z][a-z0-9]*-[^\s"'`]*)/g;

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx|css)$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith('.d.ts')) out.push(full);
  }
  return out;
}

function stripBlockComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
}

/** Line numbers inside CSS blocks whose selector is one of the allowed token selectors. */
function tokenLines(text, selectors) {
  const lines = text.split('\n');
  const allowed = new Set();
  let depth = 0;
  let inToken = false;
  let tokenDepth = 0;
  lines.forEach((line, i) => {
    const trimmed = line.trim();
    if (!inToken && depth === 0 && selectors.some((s) => trimmed === `${s} {`)) {
      inToken = true;
      tokenDepth = depth;
    }
    if (inToken) allowed.add(i + 1);
    for (const ch of line) {
      if (ch === '{') depth += 1;
      if (ch === '}') {
        depth -= 1;
        if (inToken && depth === tokenDepth) inToken = false;
      }
    }
  });
  return allowed;
}

function isAllowedByList(rel, lineText, ruleId) {
  if (allow.files.some((f) => matchGlob(f.path, rel))) return true;
  return allow.lines.some((l) => matchGlob(l.file, rel) && (!l.rule || l.rule === ruleId) && new RegExp(l.pattern).test(lineText));
}

function matchGlob(glob, rel) {
  const re = new RegExp('^' + glob.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*\*/g, '::').replace(/\*/g, '[^/]*').replace(/::/g, '.*') + '$');
  return re.test(rel);
}

const violations = [];
for (const file of walk(srcDir)) {
  const rel = relative(root, file).split(sep).join('/');
  const source = stripBlockComments(readFileSync(file, 'utf8'));
  const tokenOnly = allow.tokenBlocks[rel] ? tokenLines(source, allow.tokenBlocks[rel]) : new Set();
  source.split('\n').forEach((line, index) => {
    const lineNo = index + 1;
    if (tokenOnly.has(lineNo)) return;
    const code = line.replace(/^\s*\/\/.*$/, '');
    for (const rule of RULES) {
      rule.re.lastIndex = 0;
      const match = rule.re.exec(code);
      if (match && !isAllowedByList(rel, code, rule.id)) violations.push({ rel, lineNo, rule: rule.id, text: match[0] });
    }
    if (/\.(tsx|ts)$/.test(file)) {
      BANG_CLASS.lastIndex = 0;
      const stringsOnly = [...code.matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g)].map((m) => m[1] ?? m[2] ?? m[3] ?? '').join(' ');
      const bang = /(?:^|\s)!(?=[a-z][a-z0-9]*-)\S+/.exec(stringsOnly);
      if (bang && !isAllowedByList(rel, code, 'important')) violations.push({ rel, lineNo, rule: 'important', text: bang[0].trim() });
    }
  });
}

const counts = {};
for (const v of violations) counts[v.rule] = (counts[v.rule] || 0) + 1;
const summary = `ui-lint: ${violations.length} violation(s) ` + JSON.stringify(counts);
if (!process.argv.includes('--summary')) {
  for (const v of violations) console.log(`${v.rel}:${v.lineNo}  ${v.rule}  ${v.text}`);
}
console.log(summary);
process.exit(violations.length ? 1 : 0);
