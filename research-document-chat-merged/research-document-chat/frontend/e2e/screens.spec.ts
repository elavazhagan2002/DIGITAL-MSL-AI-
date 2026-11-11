import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { SCREENS, SHOT_VIEWPORTS, dismissDialogs, openScreen, shot, signIn, type Mode, type Role } from './helpers';

/** Every screen, every role, both modes, four viewports: screenshot + axe (contrast and serious violations) at 1440. */
for (const role of ['doctor', 'msl', 'admin'] as Role[]) {
  for (const mode of ['light', 'dark'] as Mode[]) {
    test(`screens ${role} ${mode}: screenshots and axe`, async ({ page }) => {
      test.setTimeout(240_000);
      await page.setViewportSize({ width: 1440, height: 900 });
      await signIn(page, role, mode);
      for (const screen of SCREENS[role]) {
        await page.setViewportSize({ width: 1440, height: 900 });
        await openScreen(page, screen);
        await page.waitForLoadState('networkidle').catch(() => undefined);
        await dismissDialogs(page);

        const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
        const blocking = results.violations.filter((v) => v.id === 'color-contrast' || v.impact === 'serious' || v.impact === 'critical');
        expect(
          blocking.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`),
          `${role}/${screen}/${mode} axe`,
        ).toEqual([]);

        for (const { w, h } of SHOT_VIEWPORTS) {
          await page.setViewportSize({ width: w, height: h });
          await page.waitForTimeout(200);
          await page.screenshot({ path: shot(role, screen, mode, w, h) });
        }
      }
    });
  }
}

test('touch targets are at least 44px for the shell controls', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, 'doctor', 'light');
  for (const selector of ['[data-testid="theme-toggle"]', '[data-testid="sign-out"]', '[data-testid="nav-chat"]', '[aria-label="Notifications"]']) {
    const box = await page.locator(selector).first().boundingBox();
    expect(box!.width, selector).toBeGreaterThanOrEqual(44);
    expect(box!.height, selector).toBeGreaterThanOrEqual(44);
  }
});

test('keyboard focus is visible on shell controls', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, 'doctor', 'light');
  await page.getByTestId('nav-settings').focus();
  await page.keyboard.press('Tab');
  const outline = await page.evaluate(() => {
    const cs = getComputedStyle(document.activeElement as Element);
    return { style: cs.outlineStyle, width: parseFloat(cs.outlineWidth) };
  });
  expect(outline.style).not.toBe('none');
  expect(outline.width).toBeGreaterThanOrEqual(2);
});
