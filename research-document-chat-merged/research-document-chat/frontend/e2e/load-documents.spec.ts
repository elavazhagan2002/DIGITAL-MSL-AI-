import { expect, test, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { openScreen, signIn, type Mode } from './helpers';

/**
 * Load Documents (item C). Mocked runs prove every state with its own message and no raw server text;
 * one run goes against the real backend (demo mode: IKM is unreachable from the test machine, so it
 * shows the labelled demo fixture).
 */
const CONTEXT = '**/api/v1/digital-msl/load-documents/context';
const DOCS = '**/api/v1/repositories/*/documents';
const UPLOAD = '**/api/v1/digital-msl/therapeutic-areas/*/documents/upload';

const baseCtx = {
  state: 'ok',
  code: '',
  demo: false,
  request_id: 'abc123def456',
  allow_list: { empty: false, all: false, approved_count: 1 },
  ikm: { configured: true, reachable: true, unauthorised: false, code: 'OK', checked_at: '2026-11-09T00:00:00Z' },
  missing: [],
  repositories: [] as unknown[],
};

const repos = [
  { repository_id: 'repo-1', name: 'Retina library', therapeutic_area: 'Retina and Vitreous', approved: true, document_count: 12, demo: false },
  { repository_id: 'repo-2', name: 'Diabetic eye library', therapeutic_area: 'Diabetic Retinopathy and Macular Disease', approved: false, document_count: 0, demo: false },
];

const NOW = Math.floor(Date.now() / 1000);
const docs = [
  { document_id: 'd1', original_file_name: 'Aflibercept label.pdf', status: 'completed', document_type: 'Prescribing information', upload_timestamp: NOW - 86400 },
  { document_id: 'd2', original_file_name: 'DME guideline.pdf', status: 'processing', document_type: 'Guideline', upload_timestamp: NOW - 3600 },
  { document_id: 'd3', original_file_name: 'Scan appendix.pdf', status: 'failed', document_type: 'Protocol', upload_timestamp: NOW - 7200, error_details: 'Text could not be extracted from this file.' },
];

async function fulfill(route: Route, status: number, body: unknown, headers: Record<string, string> = {}) {
  const origin = route.request().headers()['origin'] || '*';
  const cors = {
    'access-control-allow-origin': origin,
    'access-control-allow-credentials': 'true',
    'access-control-allow-headers': '*',
    'access-control-allow-methods': 'GET,POST,OPTIONS',
    'access-control-expose-headers': 'x-request-id',
    ...headers,
  };
  if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
  return route.fulfill({ status, contentType: 'application/json', headers: cors, body: JSON.stringify(body) });
}

async function mockContext(page: Page, status: number, body: unknown, delayMs = 0) {
  let calls = 0;
  await page.route(CONTEXT, async (route) => {
    calls += 1;
    if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
    await fulfill(route, status, body, { 'x-request-id': 'abc123def456' });
  });
  return () => calls;
}

async function mockDocs(page: Page, list: unknown[] = docs) {
  await page.route(DOCS, (route) => fulfill(route, 200, { documents: list, count: list.length }));
}

async function openLoadDocuments(page: Page, mode: Mode = 'light') {
  await signIn(page, 'msl', mode);
  await openScreen(page, 'literature');
  await expect(page.getByTestId('page-title')).toHaveText('Load Documents');
}

const noRawText = async (page: Page) => {
  const text = (await page.locator('body').innerText()).toLowerCase();
  for (const bad of ['bad gateway', 'dms error', 'tlsv1', 'ssl', 'traceback', '16.113.145.53', 'failed to fetch repositories']) {
    expect(text, `raw text "${bad}" must never be shown`).not.toContain(bad);
  }
};

// ------------------------------------------------------------------ mocked states
test('loading shows skeleton rows, then the page', async ({ page }) => {
  await mockContext(page, 200, { ...baseCtx, repositories: repos }, 1500);
  await mockDocs(page);
  await openLoadDocuments(page);
  await expect(page.getByTestId('loading-state')).toBeVisible();
  await expect(page.getByTestId('repository-panel')).toBeVisible();
});

test('IKM unreachable: its own message, Retry that calls again, reference id, no raw text', async ({ page }) => {
  const calls = await mockContext(page, 200, { ...baseCtx, state: 'ikm_unreachable', code: 'TLS_ERROR', ikm: { ...baseCtx.ikm, reachable: false, code: 'TLS_ERROR' } });
  await openLoadDocuments(page);
  const card = page.getByTestId('state-unreachable');
  await expect(card).toContainText("We can't reach the document store right now");
  await expect(card).toContainText('Reference: abc123def456');
  const before = calls();
  await card.getByRole('button', { name: 'Retry' }).click();
  await expect.poll(() => calls()).toBeGreaterThan(before);
  await noRawText(page);
});

test('not allowed (403) has its own message', async ({ page }) => {
  await mockContext(page, 403, { detail: 'Insufficient role for this operation.' });
  await openLoadDocuments(page);
  await expect(page.getByTestId('state-forbidden')).toContainText("You don't have access to Load Documents");
  await noRawText(page);
});

test('no repositories available says what to do', async ({ page }) => {
  await mockContext(page, 200, { ...baseCtx, state: 'no_repositories', code: 'NO_REPOSITORIES' });
  await openLoadDocuments(page);
  await expect(page.getByTestId('state-no-repositories')).toContainText('An administrator links a document repository');
});

test('not configured and unauthorised each have their own message', async ({ page }) => {
  await mockContext(page, 200, { ...baseCtx, state: 'not_configured', code: 'NOT_CONFIGURED', ikm: { ...baseCtx.ikm, configured: false, reachable: false } });
  await openLoadDocuments(page);
  await expect(page.getByTestId('state-not-configured')).toContainText('not set up yet');
});

test('unauthorised', async ({ page }) => {
  await mockContext(page, 200, { ...baseCtx, state: 'unauthorised', code: 'UNAUTHORISED', ikm: { ...baseCtx.ikm, unauthorised: true } });
  await openLoadDocuments(page);
  await expect(page.getByTestId('state-unauthorised')).toContainText('did not accept our sign-in');
  await noRawText(page);
});

test('working page: picker, limits, rejected file, upload with progress, ingestion status, table filters', async ({ page }) => {
  await mockContext(page, 200, { ...baseCtx, repositories: repos });
  let polls = 0;
  await page.route(DOCS, (route) => {
    polls += 1;
    const uploaded = polls >= 2 ? [{ document_id: 'mock-doc-1', original_file_name: 'New label.pdf', status: polls >= 4 ? 'completed' : 'processing', document_type: 'Prescribing information', upload_timestamp: NOW }] : [];
    return fulfill(route, 200, { documents: [...docs, ...uploaded] });
  });
  await page.route(UPLOAD, (route) => fulfill(route, 200, { documents: [{ document_id: 'mock-doc-1', original_file_name: 'New label.pdf', status: 'queued' }] }));
  await openLoadDocuments(page);

  // repository picker: name, therapeutic area, approved or not, count
  const cards = page.getByTestId('repo-card');
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toContainText('Retina and Vitreous');
  await expect(cards.nth(0)).toContainText('Approved');
  await expect(cards.nth(0)).toContainText('12 documents');
  await expect(cards.nth(1)).toContainText('Not approved');

  // limits are shown before anyone fails them
  await expect(page.getByTestId('dropzone')).toContainText('.pdf, .docx, .doc, .txt');
  await expect(page.getByTestId('dropzone')).toContainText('200 MB');

  // wrong type is refused on the page with its own message
  await page.getByTestId('file-input').setInputFiles({ name: 'notes.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ') });
  await expect(page.getByTestId('upload-row').first()).toContainText('Only .pdf, .docx, .doc, .txt files can be loaded.');
  await expect(page.getByTestId('upload-row').first().getByTestId('status-failed')).toBeVisible();

  // a good file: progress bar, then sent, then ingestion status follows the document store
  await page.getByTestId('file-input').setInputFiles({ name: 'New label.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 sample') });
  const row = page.getByTestId('upload-row').nth(1);
  await expect(row).toContainText('New label.pdf');
  await expect(row.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  await expect(row.getByTestId('status-queued').or(row.getByTestId('status-processing')).or(row.getByTestId('status-ready'))).toBeVisible();
  await expect(row.getByTestId('status-ready')).toBeVisible({ timeout: 20_000 });

  // metadata form: present but disabled, with the reason (backend does not accept it yet)
  await row.getByRole('button', { name: /Document details/ }).click();
  const meta = row.getByTestId('metadata-form');
  await expect(meta).toContainText('not sent to the document store yet');
  for (const label of ['Title', 'Document type', 'Version', 'Effective date', 'Expiry date', 'Approval status']) {
    await expect(meta.getByLabel(label)).toBeDisabled();
  }

  // loaded documents table: search and status filter
  const table = page.getByTestId('documents-table');
  await expect(table).toContainText('Aflibercept label.pdf');
  await page.getByPlaceholder('Search by file name').fill('guideline');
  await expect(page.getByTestId('document-row')).toHaveCount(1);
  await page.getByPlaceholder('Search by file name').fill('');
  await page.getByLabel('Filter by status').selectOption('failed');
  await expect(page.getByTestId('document-row')).toHaveCount(1);
  await expect(page.getByTestId('document-row')).toContainText('Text could not be extracted from this file.');
  await noRawText(page);
});

test('an upload that fails shows a calm message and a reference id, never raw text', async ({ page }) => {
  await mockContext(page, 200, { ...baseCtx, repositories: repos });
  await mockDocs(page);
  await page.route(UPLOAD, (route) =>
    fulfill(route, 503, { detail: 'We could not send the file to the document store. Please try again.', code: 'DOCUMENT_UPLOAD_UNAVAILABLE', request_id: 'u9f3a1b2c3d4' }, { 'x-request-id': 'u9f3a1b2c3d4' }),
  );
  await openLoadDocuments(page);
  await page.getByTestId('file-input').setInputFiles({ name: 'a.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4') });
  const row = page.getByTestId('upload-row').first();
  await expect(row).toContainText("We couldn't send the file to the document store. Please try again.");
  await expect(row).toContainText('Reference: u9f3a1b2c3d4');
  await expect(row.getByTestId('status-failed')).toBeVisible();
  await noRawText(page);
});

test('empty allow-list is explained but does not block loading', async ({ page }) => {
  await mockContext(page, 200, { ...baseCtx, allow_list: { empty: true, all: false, approved_count: 0 }, repositories: repos });
  await mockDocs(page);
  await openLoadDocuments(page);
  await expect(page.getByTestId('allow-list-note')).toContainText('No repository is on the approved list yet');
  await expect(page.getByTestId('dropzone')).toBeVisible();
});

// ------------------------------------------------------------------ real backend (demo fixture)
test('real backend: IKM is unreachable here, so the labelled demo fixture is shown and works', async ({ page }) => {
  await openLoadDocuments(page);
  await expect(page.getByTestId('demo-banner')).toContainText('Demo data');
  await expect(page.getByTestId('repo-card').first()).toContainText('Demo data');
  const table = page.getByTestId('documents-table');
  await expect(table).toContainText('Aflibercept prescribing information (sample).pdf');
  await expect(page.getByTestId('status-ready').first()).toBeVisible();
  await expect(page.getByTestId('status-processing').first()).toBeVisible();
  await expect(page.getByTestId('status-failed').first()).toBeVisible();
  await page.getByTestId('file-input').setInputFiles({ name: 'My demo upload.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 demo') });
  const row = page.getByTestId('upload-row').first();
  await expect(row).toContainText('My demo upload.pdf');
  await expect(row.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  await expect(table).toContainText('My demo upload.pdf');
  await expect(page.getByTestId('literature-disabled')).toBeVisible();
  await noRawText(page);
  const axe = await new AxeBuilder({ page }).include('[data-testid=load-documents-page]').analyze();
  expect(axe.violations.filter((v) => ['serious', 'critical'].includes(v.impact || '')), 'axe').toEqual([]);
});

// ------------------------------------------------------------------ screenshots and layout, every state
type StateShot = { name: string; setup: (page: Page) => Promise<void>; check: (page: Page) => Promise<void> };
const STATES: StateShot[] = [
  { name: 'loading', setup: async (p) => { await mockContext(p, 200, { ...baseCtx, repositories: repos }, 6000); await mockDocs(p); }, check: async (p) => { await expect(p.getByTestId('loading-state')).toBeVisible(); } },
  { name: 'unreachable', setup: async (p) => { await mockContext(p, 200, { ...baseCtx, state: 'ikm_unreachable', code: 'TLS_ERROR' }); }, check: async (p) => { await expect(p.getByTestId('state-unreachable')).toBeVisible(); } },
  { name: 'forbidden', setup: async (p) => { await mockContext(p, 403, { detail: 'x' }); }, check: async (p) => { await expect(p.getByTestId('state-forbidden')).toBeVisible(); } },
  { name: 'no-repositories', setup: async (p) => { await mockContext(p, 200, { ...baseCtx, state: 'no_repositories' }); }, check: async (p) => { await expect(p.getByTestId('state-no-repositories')).toBeVisible(); } },
  { name: 'not-configured', setup: async (p) => { await mockContext(p, 200, { ...baseCtx, state: 'not_configured' }); }, check: async (p) => { await expect(p.getByTestId('state-not-configured')).toBeVisible(); } },
  { name: 'working', setup: async (p) => { await mockContext(p, 200, { ...baseCtx, allow_list: { empty: true, all: false, approved_count: 0 }, repositories: repos }); await mockDocs(p); }, check: async (p) => { await expect(p.getByTestId('documents-table')).toBeVisible(); } },
];
const SHOTS = [
  { w: 1440, h: 900 },
  { w: 1920, h: 1080 },
  { w: 768, h: 1024 },
  { w: 360, h: 800 },
];

for (const mode of ['light', 'dark'] as Mode[]) {
  for (const vp of SHOTS) {
    for (const state of STATES) {
      test(`screenshot ${state.name} ${mode} ${vp.w}x${vp.h}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.w, height: vp.h });
        await state.setup(page);
        await openLoadDocuments(page, mode);
        await state.check(page);
        await page.waitForTimeout(200);
        const scrolls = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
        expect(scrolls, 'no horizontal page scroll').toBe(false);
        await page.screenshot({ path: `../docs/screenshots/fixC/msl-load-documents-${state.name}-${mode}-${vp.w}x${vp.h}.png`, fullPage: false });
      });
    }
  }
}

