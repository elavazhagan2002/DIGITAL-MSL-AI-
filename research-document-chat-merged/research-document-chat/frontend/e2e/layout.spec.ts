import { expect, test, type Page } from '@playwright/test';
import { VIEWPORTS, signIn, type Mode, type Role } from './helpers';

/** Elements that must never intersect each other. Containers (panel, thread parents) are not listed. */
const COMMON: Array<[string, string]> = [
  ['logo', '#app-sidebar img[alt="Sciagen"]'],
  ['header title', '[data-testid="page-title"]'],
  ['sidebar user', '[data-testid="sidebar-user"]'],
  ['sign out', '[data-testid="sign-out"]'],
];
const DOCTOR_ONLY: Array<[string, string]> = [
  ['chats heading', '[data-testid="chats-heading"]'],
  ['area header', '[data-testid="chat-area-header"]'],
  ['composer disclaimer', '[data-testid="composer-disclaimer"]'],
  ['thread', '[data-testid="chat-thread"]'],
  ['composer', '[data-testid="chat-composer"]'],
];

interface Box {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

async function collect(page: Page, role: Role): Promise<Box[]> {
  const named = role === 'doctor' ? [...COMMON, ...DOCTOR_ONLY] : COMMON;
  return page.evaluate(
    ({ named }) => {
      const out: Box[] = [];
      const visible = (el: Element) => {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
      };
      const add = (name: string, el: Element) => {
        if (!visible(el)) return;
        const r = el.getBoundingClientRect();
        out.push({ name, x: r.x, y: r.y, w: r.width, h: r.height });
      };
      for (const [name, selector] of named) document.querySelectorAll(selector).forEach((el) => add(name, el));
      document.querySelectorAll('[data-testid^="nav-"]').forEach((el) => add(`nav ${el.textContent?.trim()}`, el));
      return out;
    },
    { named },
  ) as Promise<Box[]>;
}

function intersects(a: Box, b: Box) {
  const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return overlapX > 1 && overlapY > 1;
}

for (const mode of ['light', 'dark'] as Mode[]) {
  for (const role of ['doctor', 'msl', 'admin'] as Role[]) {
    test(`no overlaps, no negative coordinates, no horizontal scroll: ${role} ${mode}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await signIn(page, role, mode);
      if (role === 'doctor') await page.getByTestId('chat-composer').waitFor();
      for (const { w, h } of VIEWPORTS) {
        await page.setViewportSize({ width: w, height: h });
        await page.waitForTimeout(250);
        const boxes = await collect(page, role);
        expect(boxes.length, `${role} ${mode} ${w}x${h}: elements found`).toBeGreaterThan(w >= 960 ? 3 : 0);
        for (const box of boxes) {
          expect(box.x, `${box.name} x at ${w}x${h}`).toBeGreaterThanOrEqual(-0.5);
          expect(box.y, `${box.name} y at ${w}x${h}`).toBeGreaterThanOrEqual(-0.5);
          expect(box.x + box.w, `${box.name} right edge at ${w}x${h}`).toBeLessThanOrEqual(w + 0.5);
        }
        for (let i = 0; i < boxes.length; i += 1) {
          for (let j = i + 1; j < boxes.length; j += 1) {
            expect(
              intersects(boxes[i], boxes[j]),
              `${boxes[i].name} overlaps ${boxes[j].name} at ${w}x${h} (${role}, ${mode})`,
            ).toBe(false);
          }
        }
        const overflow = await page.evaluate(() => ({
          sw: document.documentElement.scrollWidth,
          iw: window.innerWidth,
          sh: document.documentElement.scrollHeight,
          ih: window.innerHeight,
        }));
        expect(overflow.sw, `horizontal scroll at ${w}x${h}`).toBeLessThanOrEqual(overflow.iw);
        expect(overflow.sh, `page double-scroll at ${w}x${h}`).toBeLessThanOrEqual(overflow.ih + 1);
        if (role === 'doctor') {
          const composer = boxes.find((b) => b.name === 'composer')!;
          expect(composer.y + composer.h, `composer visible at ${w}x${h}`).toBeLessThanOrEqual(h + 0.5);
        }
      }
    });
  }
}

test('the logo exists once; the drawer is not visible at 1440px; the close button is not rendered', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, 'doctor', 'light');
  await expect(page.locator('img[alt="Sciagen"]:visible')).toHaveCount(1);
  expect(await page.locator('img[alt="Sciagen"]').count()).toBe(1);
  await expect(page.getByRole('button', { name: 'Close menu' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Open menu' })).toBeHidden();
  await expect(page.locator('#app-sidebar')).toBeVisible();
  const width = await page.locator('#app-sidebar').evaluate((el) => el.getBoundingClientRect().width);
  expect(Math.round(width)).toBe(264);
});

test('below 960px the sidebar is an off-canvas drawer opened from a 44px menu button', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await signIn(page, 'doctor', 'light');
  await expect(page.locator('#app-sidebar')).toBeHidden();
  const menu = page.getByRole('button', { name: 'Open menu' });
  const box = await menu.boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);
  await menu.click();
  await expect(page.locator('#app-sidebar')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Close menu' })).toBeVisible();
  await expect(page.locator('img[alt="Sciagen"]:visible')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(page.locator('#app-sidebar')).toBeHidden();
});

for (const mode of ['light', 'dark'] as Mode[]) {
  test(`page background is never pure black or white and the sidebar uses its token (${mode})`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, 'doctor', mode);
    const result = await page.evaluate(() => {
      const probe = document.createElement('div');
      probe.style.background = 'var(--navy)';
      document.body.appendChild(probe);
      const navy = getComputedStyle(probe).backgroundColor;
      probe.remove();
      const bgs = [document.documentElement, document.body, document.querySelector('#root > div') as Element, document.querySelector('main') as Element].map(
        (el) => getComputedStyle(el).backgroundColor,
      );
      const sidebar = getComputedStyle(document.querySelector('#app-sidebar')!).backgroundColor;
      return { navy, sidebar, bgs };
    });
    for (const bg of result.bgs) {
      expect(bg).not.toBe('rgb(0, 0, 0)');
      expect(bg).not.toBe('rgb(255, 255, 255)');
    }
    expect(result.sidebar).toBe(result.navy);
    expect(result.sidebar).not.toBe('rgb(0, 0, 0)');
    const htmlBg = result.bgs[0];
    expect(htmlBg).toBe(mode === 'dark' ? 'rgb(5, 7, 22)' : 'rgb(242, 246, 250)');
  });
}
