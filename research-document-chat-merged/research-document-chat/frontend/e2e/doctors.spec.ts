import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { VIEWPORTS, dismissDialogs, signIn, type Mode, type Role } from './helpers';

// The machine runs several dev servers and test suites at once; one retry absorbs scheduling stalls.
test.describe.configure({ retries: 1 });

const SHOTS = '../docs/screenshots/fixAB';
const SHOT_SIZES = [
  { w: 1440, h: 900 },
  { w: 1920, h: 1080 },
  { w: 768, h: 1024 },
  { w: 360, h: 800 },
] as const;
const ROLES: Array<{ role: Role; label: string }> = [
  { role: 'msl', label: 'physical-msl' },
  { role: 'admin', label: 'administrator' },
];
const MANAGED_AREAS = [
  'Retina and Vitreous',
  'Diabetic Retinopathy and Macular Disease',
  'Glaucoma',
  'Cornea and External Disease',
];
const LEGACY_NAMES = ['Eye_Core_Retina-222e', 'General_Research_Repository', 'Retina-Product-A', 'Seed stand-in'];

/** The Physical MSL queue summary pops up a moment after sign-in and would cover the page. */
async function closeWelcome(page: Page) {
  await page.getByRole('dialog').first().waitFor({ timeout: 4000 }).catch(() => undefined);
  await dismissDialogs(page);
}

async function openDoctors(page: Page, role: Role, mode: Mode, size: { w: number; h: number }) {
  await page.setViewportSize({ width: size.w, height: size.h });
  await signIn(page, role, mode);
  if (role === 'msl') await closeWelcome(page);
  if (!(await page.getByTestId('nav-doctors').isVisible())) await page.getByRole('button', { name: 'Open menu' }).click();
  await page.getByTestId('nav-doctors').click();
  await expect(page.getByTestId('page-title')).toHaveText('Registered Doctors');
  if (role === 'msl') await closeWelcome(page);
  await expect(page.getByTestId('result-count')).toContainText('Showing', { timeout: 30_000 });
}

const rowsLocator = (page: Page, size: { w: number }) => page.getByTestId(size.w < 640 ? 'doctor-card' : 'doctor-row');

for (const { role, label } of ROLES) {
  test.describe(`B. Registered Doctors (${label})`, () => {
    for (const size of VIEWPORTS) {
      test(`${size.w}x${size.h}: no horizontal page scroll, one-line badges, pagination inside the viewport above the footer`, async ({ page }) => {
        test.setTimeout(120_000);
        await openDoctors(page, role, 'light', size);
        const rows = rowsLocator(page, size);
        await expect(rows.first()).toBeVisible();
        expect(await rows.count()).toBeGreaterThanOrEqual(10);

        const hScroll = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1 || document.body.scrollWidth > window.innerWidth + 1);
        expect(hScroll, 'the page must not scroll horizontally').toBe(false);

        // status badges never wrap: every pill is a single line
        const heights = await page.evaluate((cardMode) => {
          const root = cardMode ? '[data-testid="doctor-card"]' : '[data-testid="doctor-row"]';
          return [...document.querySelectorAll(`${root} span.rounded-full`)].map((el) => Math.round(el.getBoundingClientRect().height));
        }, size.w < 640);
        expect(heights.length).toBeGreaterThan(10);
        expect(Math.max(...heights), 'a status/area/consent pill wrapped onto two lines').toBeLessThanOrEqual(30);

        // rows are 56px in the table
        if (size.w >= 640) {
          const rowHeight = (await rows.first().boundingBox())!.height;
          expect(Math.round(rowHeight)).toBe(56); // 56px rows
        }

        // pagination and the count are inside the card, fully visible above the footer once scrolled to the end
        await page.evaluate(() => document.getElementById('main-content')?.scrollTo({ top: 1e6 }));
        await page.waitForTimeout(200);
        const pagination = (await page.getByTestId('doctors-pagination').boundingBox())!;
        const footer = (await page.locator('footer').boundingBox())!;
        const card = (await page.getByTestId('doctors-card').boundingBox())!;
        expect(pagination.y).toBeGreaterThanOrEqual(0);
        expect(pagination.y + pagination.height).toBeLessThanOrEqual(footer.y + 1);
        expect(pagination.y + pagination.height).toBeLessThanOrEqual(card.y + card.height + 1);
        expect(footer.y + footer.height).toBeLessThanOrEqual(size.h + 1);
        await expect(page.getByTestId('result-count')).toHaveText(/^Showing 1-\d+ of \d+$/);
      });
    }

    test('row click opens the detail drawer; Esc closes it; read-only for the Physical MSL', async ({ page }) => {
      test.setTimeout(120_000);
      const size = { w: 1440, h: 900 };
      await openDoctors(page, role, 'light', size);
      await page.getByTestId('doctor-row').nth(3).click();
      const drawer = page.getByTestId('doctor-drawer');
      await expect(drawer).toBeVisible();
      await expect(drawer).toContainText('Registration');
      await expect(drawer.getByText('NMC Unique ID')).toBeVisible();
      await expect(drawer.getByText('Last contact')).toBeVisible();
      await expect(drawer.getByText('Recorded by')).toBeVisible();
      await expect(drawer.getByTestId('consent-history-note')).toBeVisible();
      if (role === 'msl') {
        await expect(drawer).toContainText('Read-only for Physical MSL');
        await expect(drawer.getByRole('button', { name: 'Verify' })).toHaveCount(0);
      } else {
        await expect(drawer.getByRole('button', { name: 'Verify' })).toBeVisible();
      }
      await page.keyboard.press('Escape');
      await expect(drawer).toHaveCount(0);
      await expect(page.getByTestId('doctor-row').nth(3)).toBeVisible();
    });

    test('area chips show only therapeutic-area names, never repository names or ids', async ({ page }) => {
      test.setTimeout(120_000);
      await openDoctors(page, role, 'light', { w: 1440, h: 900 });
      const chips = await page.getByTestId('areas-cell').allInnerTexts();
      expect(chips.length).toBeGreaterThan(5);
      const text = chips.join(' | ');
      for (const bad of LEGACY_NAMES) expect(text).not.toContain(bad);
      expect(text).not.toMatch(/pending:|legacy-repo|[0-9a-f]{8}-[0-9a-f]{4}/);
      for (const chip of await page.locator('[data-testid="areas-cell"] span.rounded-full span.truncate').allInnerTexts()) {
        expect(MANAGED_AREAS.some((n) => n.startsWith(chip.replace(/…$/, '')))).toBe(true);
      }
      // a doctor whose only areas were repository names shows "No area"
      await expect(page.getByTestId('areas-cell').filter({ hasText: 'No area' }).first()).toBeVisible();
      // the +N popover lists the remaining therapeutic areas
      await page.getByRole('button', { name: /more therapeutic areas?/ }).first().click();
      const pop = page.getByTestId('areas-popover');
      await expect(pop).toBeVisible();
      const popText = await pop.innerText();
      for (const bad of LEGACY_NAMES) expect(popText).not.toContain(bad);
      await page.keyboard.press('Escape');
      await expect(pop).toHaveCount(0);
    });

    test('filters, quick-filter strip, sorting and the same name shown twice with different emails', async ({ page }) => {
      test.setTimeout(120_000);
      await openDoctors(page, role, 'light', { w: 1440, h: 900 });
      // two doctors can share a name; the email tells them apart
      const names = await page.locator('[data-testid="doctor-row"] button span.font-semibold').allInnerTexts();
      expect(names.filter((n) => n === 'Dr. Anita Rao').length).toBe(2);
      const emails = await page.locator('[data-testid="doctor-row"] button span.text-xs').allInnerTexts();
      expect(emails).toEqual(expect.arrayContaining(['anita.rao@lotuseye.example', 'a.rao@citysight.example']));
      // quick filter from the strip
      await page.getByRole('button', { name: /Needs info/ }).click();
      await expect(page.getByTestId('result-count')).toHaveText('Showing 1-1 of 1');
      await expect(page.getByTestId('clear-filters')).toBeVisible();
      await page.getByTestId('clear-filters').click();
      await expect(page.getByTestId('result-count')).toHaveText(/^Showing 1-\d+ of 1\d$/);
      // search by registration number
      await page.getByLabel('Search doctors').fill('MH-77821');
      await expect(page.getByTestId('result-count')).toHaveText('Showing 1-1 of 1');
      await page.getByTestId('clear-filters').click();
      // sortable Registered
      const header = page.getByRole('columnheader', { name: /Registered/ });
      await header.getByRole('button').click();
      await expect(header).toHaveAttribute('aria-sort', 'ascending');
      await header.getByRole('button').click();
      await expect(header).toHaveAttribute('aria-sort', 'descending');
      // filters sit on one row at 1440
      const ys = await Promise.all(['doctor-search', 'doctor-status', 'doctor-area', 'doctor-city'].map(async (id) => (await page.locator(`#${id}`).boundingBox())!.y));
      expect(Math.max(...ys) - Math.min(...ys)).toBeLessThan(6);
    });

    test('states: loading skeleton, empty, error with Retry and no raw text', async ({ page }) => {
      test.setTimeout(120_000);
      const size = { w: 1440, h: 900 };
      let release!: () => void;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      await page.route('**/digital-msl/doctors?*', async (route) => {
        await gate; // hold the response until the skeleton has been seen
        await route.continue();
      });
      await page.setViewportSize({ width: size.w, height: size.h });
      await signIn(page, role, 'light');
      if (role === 'msl') await closeWelcome(page);
      await page.getByTestId('nav-doctors').click();
      await expect(page.getByTestId('doctor-skeleton-row').first()).toBeVisible();
      await page.screenshot({ path: `${SHOTS}/doctors-${label}-loading-light-1440x900.png` });
      release();
      await expect(page.getByTestId('doctor-row').first()).toBeVisible({ timeout: 30_000 });
      await page.unroute('**/digital-msl/doctors?*');

      await page.route('**/digital-msl/doctors?*', (route) =>
        route.fulfill({ json: { doctors: [], counts: { total: 0, verified: 0, pending: 0, needs_info: 0, rejected: 0, pending_contact: 0 }, total: 0, page: 1, page_size: 20 } }),
      );
      await page.getByRole('button', { name: 'Refresh' }).click();
      await expect(page.getByTestId('doctors-empty')).toBeVisible();
      await expect(page.getByTestId('doctors-empty')).toContainText('No doctors have registered yet');
      await page.screenshot({ path: `${SHOTS}/doctors-${label}-empty-light-1440x900.png` });

      await page.unroute('**/digital-msl/doctors?*');
      await page.route('**/digital-msl/doctors?*', (route) =>
        route.fulfill({ status: 503, json: { detail: "We can't load your knowledge sources right now. Please try again.", code: 'KNOWLEDGE_SOURCES_UNAVAILABLE', request_id: 'abc123def456' } }),
      );
      await page.reload();
      if (role === 'msl') await closeWelcome(page);
      await page.getByTestId('nav-doctors').click();
      const err = page.getByTestId('error-card');
      await expect(err).toBeVisible({ timeout: 30_000 });
      await expect(err.getByRole('button', { name: 'Retry' })).toBeVisible();
      expect((await page.locator('body').innerText()).toLowerCase()).not.toContain('bad gateway');
      await page.screenshot({ path: `${SHOTS}/doctors-${label}-error-light-1440x900.png` });
    });

    for (const mode of ['light', 'dark'] as Mode[]) {
      test(`axe, no contrast violations (${mode}), table and drawer`, async ({ page }) => {
        test.setTimeout(120_000);
        await openDoctors(page, role, mode, { w: 1440, h: 900 });
        let results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
        expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' ; ')}`), 'table').toEqual([]);
        await page.getByTestId('doctor-row').nth(3).click();
        await expect(page.getByTestId('doctor-drawer')).toBeVisible();
        await page.waitForTimeout(300);
        results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
        expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' ; ')}`), 'drawer').toEqual([]);
      });
    }

    for (const mode of ['light', 'dark'] as Mode[]) {
      test(`screenshots (${mode}): table, drawer, 360 stacked cards`, async ({ browser }) => {
        test.setTimeout(300_000);
        for (const size of SHOT_SIZES) {
          const context = await browser.newContext({ viewport: { width: size.w, height: size.h }, serviceWorkers: 'block' });
          const page = await context.newPage();
          await openDoctors(page, role, mode, size);
          await page.waitForTimeout(300);
          await page.screenshot({ path: `${SHOTS}/doctors-${label}-table-${mode}-${size.w}x${size.h}.png` });
          if (size.w === 360 || size.w === 1440 || size.w === 1920) {
            await rowsLocator(page, size).nth(3).click();
            await expect(page.getByTestId('doctor-drawer')).toBeVisible();
            await expect(page.getByTestId('doctor-drawer')).toContainText('Registration', { timeout: 20_000 });
            await page.waitForTimeout(300);
            await page.screenshot({ path: `${SHOTS}/doctors-${label}-drawer-${mode}-${size.w}x${size.h}.png` });
          }
          await context.close();
        }
      });
    }
  });
}
