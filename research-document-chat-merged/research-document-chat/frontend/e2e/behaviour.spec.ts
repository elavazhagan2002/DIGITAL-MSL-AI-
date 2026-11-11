import { expect, test } from '@playwright/test';
import { dismissDialogs, openScreen, signIn, type Role } from './helpers';

function parseRgb(value: string): [number, number, number, number] {
  const m = /rgba?\(([^)]+)\)/.exec(value)!;
  const parts = m[1].split(',').map((p) => parseFloat(p.trim()));
  return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
}

function luminance([r, g, b]: number[]) {
  const f = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(fg: number[], bg: number[]) {
  const [a, b] = [luminance(fg), luminance(bg)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

/** alpha-composite `top` over an opaque `bottom` colour */
function over(top: number[], bottom: number[]) {
  const a = top[3] ?? 1;
  return [0, 1, 2].map((i) => Math.round(top[i] * a + bottom[i] * (1 - a)));
}

for (const mode of ['light', 'dark'] as const) {
  test(`sidebar: nav text contrast, active state and secondary Sign out (${mode})`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, 'doctor', mode);
    const sidebarBg = parseRgb(await page.locator('#app-sidebar').evaluate((el) => getComputedStyle(el).backgroundColor));
    expect(sidebarBg[3]).toBe(1);

    const active = page.locator('#app-sidebar [data-active="true"]');
    await expect(active).toHaveCount(1);
    await expect(active).toHaveAttribute('aria-current', 'page');
    const activeStyle = await active.evaluate((el) => {
      const cs = getComputedStyle(el);
      return { bg: cs.backgroundColor, color: cs.color, border: cs.borderLeftColor, borderW: cs.borderLeftWidth };
    });
    expect(parseRgb(activeStyle.bg)).toEqual([2, 180, 216, 0.18]); // rgba(2,180,216,.18)
    expect(parseRgb(activeStyle.color).slice(0, 3)).toEqual([255, 255, 255]); // white text
    expect(parseRgb(activeStyle.border).slice(0, 3)).toEqual([2, 180, 216]); // cyan indicator
    expect(activeStyle.borderW).toBe('3px');

    // every nav label is readable on the navy sidebar (>= 4.5:1)
    const labels = await page.locator('#app-sidebar nav button').evaluateAll((els) =>
      els.map((el) => ({ text: el.textContent?.trim() ?? '', color: getComputedStyle(el).color, bg: getComputedStyle(el).backgroundColor })),
    );
    for (const l of labels) {
      const bg = over(parseRgb(l.bg), sidebarBg);
      expect(contrast(parseRgb(l.color), bg), `${l.text} contrast`).toBeGreaterThanOrEqual(4.5);
    }
    const nav = await page.getByTestId('nav-chat').boundingBox();
    expect(nav!.height).toBeGreaterThanOrEqual(44);

    // user name is white on navy; Sign out is an outline button, not red
    const user = parseRgb(await page.getByTestId('sidebar-user').evaluate((el) => getComputedStyle(el).color));
    expect(user.slice(0, 3)).toEqual([255, 255, 255]);
    const out = await page.getByTestId('sign-out').evaluate((el) => {
      const cs = getComputedStyle(el);
      return { bg: cs.backgroundColor, border: cs.borderTopColor, color: cs.color, width: cs.borderTopWidth };
    });
    expect(parseRgb(out.bg)[3]).toBe(0); // transparent
    expect(parseRgb(out.border)).toEqual([255, 255, 255, 0.5]);
    expect(['1px', '1.5px']).toContain(out.width); // 1.5px in CSS; browsers snap it to whole device pixels
    const [r, g, b] = parseRgb(out.color);
    expect(r > g * 1.4 && r > b * 1.4, 'Sign out text must not be red').toBe(false);
  });
}

test('theme toggle switches and persists across a reload', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, 'doctor', 'light');
  const toggle = page.getByTestId('theme-toggle');
  await expect(toggle).toHaveAccessibleName('Switch to dark mode');
  await toggle.click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  expect(await page.evaluate(() => localStorage.getItem('dmsl_theme'))).toBe('dark');
  await page.reload();
  await page.getByTestId('page-title').waitFor();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect(page.getByTestId('theme-toggle')).toHaveAccessibleName('Switch to light mode');
});

test('toasts: the same failure shows one toast, with calm text', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, 'doctor', 'light');
  await openScreen(page, 'settings');
  await page.getByTestId('settings-nav-consent').click(); // the consent switches live in their own section now
  await page.route('**/portal/consents', (route) =>
    route.fulfill({ status: 502, contentType: 'text/plain', body: 'Bad Gateway' }),
  );
  const toggle = page.getByRole('switch', { name: /WhatsApp.*consent/ });
  await toggle.waitFor();
  for (let i = 0; i < 3; i += 1) {
    await toggle.click();
    await page.waitForTimeout(150);
  }
  await expect(page.getByTestId('toast')).toHaveCount(1);
  const text = (await page.getByTestId('toast').innerText()).toLowerCase();
  expect(text).not.toContain('bad gateway');
  await expect(page.getByTestId('toast')).toHaveAttribute('role', 'alert');
  const close = await page.getByRole('button', { name: 'Dismiss notification' }).boundingBox();
  expect(close!.width).toBeGreaterThanOrEqual(44);
  expect(close!.height).toBeGreaterThanOrEqual(44);
});

test('doctor chat: a failing /me/context shows one friendly inline message, Retry, a reference and no raw text', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  let calls = 0;
  await page.route('**/api/v1/digital-msl/me/context', (route) => {
    calls += 1;
    return route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: "We can't load your knowledge sources right now. Please try again.",
        code: 'KNOWLEDGE_SOURCES_UNAVAILABLE',
        request_id: 'req0a1b2c3d4e',
      }),
    });
  });
  await signIn(page, 'doctor', 'light');
  const card = page.getByTestId('error-card');
  await expect(card).toBeVisible({ timeout: 15_000 });
  await expect(card).toContainText("We can't load your knowledge sources right now. Please try again.");
  await expect(card).toContainText('req0a1b2c3d4e');
  await expect(card.getByRole('button', { name: 'Retry' })).toBeVisible();
  const body = (await page.locator('body').innerText()).toLowerCase();
  for (const raw of ['bad gateway', 'dms error', 'failed to fetch', 'traceback']) expect(body).not.toContain(raw);
  await expect(page.getByTestId('toast')).toHaveCount(0);
  expect(calls, 'one request plus at most two automatic retries').toBeLessThanOrEqual(3);

  // Retry works once the service is back
  await page.unroute('**/api/v1/digital-msl/me/context');
  await card.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByTestId('chat-composer')).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: '../docs/screenshots/shell/doctor-chat-after-retry-light-1440x900.png' });
});

test('doctor chat: the 503 error state is captured for the record', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route('**/api/v1/digital-msl/me/context', (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: "We can't load your knowledge sources right now. Please try again.", code: 'KNOWLEDGE_SOURCES_UNAVAILABLE', request_id: 'req0a1b2c3d4e' }),
    }),
  );
  for (const mode of ['light', 'dark'] as const) {
    await signIn(page, 'doctor', mode);
    await expect(page.getByTestId('error-card')).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: `../docs/screenshots/shell/doctor-ikm-503-${mode}-1440x900.png` });
    await page.getByTestId('sign-out').click();
    await page.getByRole('button', { name: 'Doctor' }).first().waitFor();
  }
});

test('physical MSL: a 502 from the repository list shows one friendly message (never "Bad Gateway")', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route('**/api/v1/repositories', (route) => route.fulfill({ status: 502, contentType: 'text/plain', body: 'Bad Gateway' }));
  await signIn(page, 'msl', 'light');
  await openScreen(page, 'literature');
  const card = page.getByTestId('error-card');
  await expect(card).toBeVisible({ timeout: 15_000 });
  await expect(card).toContainText("We can't load your knowledge sources right now. Please try again.");
  await expect(card.getByRole('button', { name: 'Retry' })).toBeVisible();
  expect((await page.locator('body').innerText()).toLowerCase()).not.toContain('bad gateway');
  expect(await page.getByTestId('toast').count()).toBeLessThanOrEqual(1);
});

test('demo doctor: areas come from /me/context and the chat is usable with an empty allow-list', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const contextResponse = page.waitForResponse((r) => r.url().includes('/digital-msl/me/context') && r.status() === 200);
  await signIn(page, 'doctor', 'light');
  const body = await (await contextResponse).json();
  expect(body.areas.map((a: { name: string }) => a.name)).toEqual(['Retina and Vitreous', 'Diabetic Retinopathy and Macular Disease']);
  expect(body.areas[0].is_default).toBe(true);
  expect(body.routed_to_msl).toBe(true);
  expect(body.verification_status).toBe('VERIFIED');

  await expect(page.getByTestId('chat-area-name')).toHaveText('Retina and Vitreous');
  // the routing sentence lives in the first-conversation notice, not in a permanent banner
  await expect(page.getByTestId('routed-to-msl')).toHaveCount(0);
  await expect(page.getByTestId('notice-card')).toContainText('Your question will be reviewed by a Physical MSL.');
  await expect(page.getByTestId('no-areas')).toHaveCount(0);
  await expect(page.getByText('Ask an administrator to create a therapeutic area')).toHaveCount(0);

  // the doctor can type and send; the answer is the standard escalation reply, not an error
  const input = page.getByLabel('Your question');
  await expect(input).toBeEnabled();
  await input.fill('What is the effect of aflibercept on central subfield thickness in diabetic macular edema?');
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect(page.getByTestId('chat-thread')).toContainText('could not find this in the approved sources', { timeout: 60_000 });
  await expect(page.getByTestId('toast')).toHaveCount(0);
});

test.describe('login screen', () => {
  test('demo mode fills credentials per role and the register box sits below the primary button (Doctor only)', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/');
    const email = page.getByLabel('Work email');
    const name = page.getByLabel('Name');
    await expect(page.getByText('Demo mode: sample credentials filled in')).toBeVisible();
    await expect(email).toHaveValue('doctor@demo.example');
    await expect(name).toHaveValue('Demo Doctor');
    await page.getByRole('button', { name: /Physical MSL/ }).first().click();
    await expect(email).toHaveValue('msl@demo.example');
    await expect(name).toHaveValue('Demo Physical MSL');
    await expect(page.getByText('New to Digital MSL?')).toHaveCount(0);
    await page.getByRole('button', { name: /Administrator/ }).first().click();
    await expect(email).toHaveValue('admin@demo.example');
    await page.getByRole('button', { name: /^Doctor Ask clinical/ }).click();
    await expect(email).toHaveValue('doctor@demo.example');
    const primary = await page.getByRole('button', { name: /Continue as Doctor/ }).boundingBox();
    const register = page.getByText('New to Digital MSL?');
    const box = await register.boundingBox();
    expect(box!.y).toBeGreaterThan(primary!.y + primary!.height - 1);
    const registerButton = await page.getByRole('button', { name: 'Doctor? Register here' }).boundingBox();
    expect(registerButton!.y + registerButton!.height).toBeLessThanOrEqual(768);
  });

  test('each role signs in and lands on its home screen', async ({ page }) => {
    const expected: Record<Role, string> = { doctor: 'Talk to Digital MSL', msl: 'My Queue', admin: 'Home' };
    for (const role of ['doctor', 'msl', 'admin'] as Role[]) {
      await page.context().clearCookies();
      await page.setViewportSize({ width: 1440, height: 900 });
      await signIn(page, role, 'light');
      await expect(page.getByTestId('page-title')).toHaveText(expected[role]);
      await dismissDialogs(page);
      await page.getByTestId('sign-out').click();
      await page.getByRole('button', { name: /Continue as/ }).waitFor();
    }
  });
});
