import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { dismissDialogs, openScreen, signIn, type Mode, type Role } from './helpers';

/** Sections per role, as shown in the sub-navigation (data-section ids). */
const SECTIONS: Record<Role, string[]> = {
  doctor: ['profile', 'security', 'areas', 'consent', 'registration', 'notifications', 'preferences', 'activity'],
  msl: ['profile', 'security', 'wording', 'myareas', 'notifications', 'preferences', 'activity'],
  admin: ['users', 'adminareas', 'ikm', 'notice', 'scope', 'integrations', 'audit', 'environment', 'profile', 'security', 'notifications', 'preferences', 'activity'],
};
const ROLES: Role[] = ['doctor', 'msl', 'admin'];
const LAYOUT_VIEWPORTS = [
  { w: 1920, h: 1080 },
  { w: 1606, h: 900 },
  { w: 1440, h: 900 },
  { w: 1366, h: 768 },
  { w: 1024, h: 768 },
  { w: 768, h: 1024 },
  { w: 360, h: 800 },
];
const SHOT_VIEWPORTS = [
  { w: 1920, h: 1080 },
  { w: 1440, h: 900 },
  { w: 1366, h: 768 },
  { w: 1024, h: 768 },
  { w: 768, h: 1024 },
  { w: 360, h: 800 },
];

const shot = (role: Role, section: string, mode: Mode, w: number, h: number) =>
  `../docs/screenshots/fixD/${role}-${section}-${mode}-${w}x${h}.png`;

async function openSettings(page: Page, role: Role, mode: Mode = 'light') {
  await signIn(page, role, mode);
  await openScreen(page, 'settings');
  await page.getByTestId('settings-root').waitFor();
}

async function gotoSection(page: Page, id: string) {
  await page.getByTestId(`settings-nav-${id}`).click();
  await page.waitForTimeout(250);
  await page.waitForLoadState('networkidle').catch(() => undefined);
}

// ------------------------------------------------------------------ shell on the Settings route
for (const role of ROLES) {
  test(`shell ${role}: the Settings route is inside the app shell (sidebar from 960px, menu button below)`, async ({ page }) => {
    await openSettings(page, role);
    for (const { w, h } of LAYOUT_VIEWPORTS) {
      await page.setViewportSize({ width: w, height: h });
      await page.waitForTimeout(200);
      const info = await page.evaluate(() => {
        const vis = (el: Element | null) => !!el && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0;
        const sidebar = document.querySelector('#app-sidebar') as HTMLElement | null;
        return {
          sidebarVisible: vis(sidebar),
          sidebarBg: sidebar ? getComputedStyle(sidebar).backgroundColor : '',
          menuVisible: vis(document.querySelector('[aria-label="Open menu"]')),
          logos: document.querySelectorAll('img[alt="Sciagen"]').length,
          title: document.querySelector('[data-testid=page-title]')?.textContent,
        };
      });
      const label = `${role}@${w}x${h}`;
      expect(info.title, label).toBe('Settings');
      expect(info.logos, `${label} logo once`).toBe(1);
      if (w >= 960) {
        expect(info.sidebarVisible, `${label} sidebar visible`).toBe(true);
        expect(info.sidebarBg, `${label} sidebar is navy`).not.toBe('rgba(0, 0, 0, 0)');
        expect(info.menuVisible, `${label} no menu button on desktop`).toBe(false);
      } else {
        expect(info.sidebarVisible, `${label} sidebar hidden below 960`).toBe(false);
        expect(info.menuVisible, `${label} menu button below 960`).toBe(true);
      }
    }
  });
}

// ------------------------------------------------------------------ layout
for (const role of ROLES) {
  test(`layout ${role}: nothing past the viewport, nav never touches content, content fills the column`, async ({ page }) => {
    test.setTimeout(300_000);
    await openSettings(page, role);
    for (const { w, h } of LAYOUT_VIEWPORTS) {
      await page.setViewportSize({ width: w, height: h });
      await page.waitForTimeout(200);
      for (const id of SECTIONS[role]) {
        await gotoSection(page, id);
        const m = await page.evaluate(() => {
          const rect = (sel: string) => document.querySelector(sel)!.getBoundingClientRect();
          const root = rect('[data-testid=settings-root]');
          const band = rect('[data-testid=settings-account-band]');
          const content = rect('[data-testid=settings-content]');
          const nav = document.querySelector('[data-testid=settings-nav]') as HTMLElement;
          const navRect = nav.getBoundingClientRect();
          const items = Array.from(nav.querySelectorAll('button')).map((b) => b.getBoundingClientRect());
          const widest = items.reduce((a, r) => Math.max(a, r.right), 0);
          const lowest = items.reduce((a, r) => Math.max(a, r.bottom), 0);
          return {
            rootLeft: root.left,
            rootRight: root.right,
            rootWidth: root.width,
            bandWidth: band.width,
            contentLeft: content.left,
            contentRight: content.right,
            contentWidth: content.width,
            contentTop: content.top,
            navWidth: navRect.width,
            navVertical: getComputedStyle(nav).flexDirection === 'column',
            navItemsRight: widest,
            navItemsBottom: lowest,
            navRect: { left: navRect.left, right: navRect.right, bottom: navRect.bottom },
            hasAside: !!document.querySelector('[data-testid=settings-aside]'),
            hScroll: document.documentElement.scrollWidth > window.innerWidth + 1,
            negative: [root, band, content].some((r) => r.left < -1),
            pastViewport: [root, band, content].some((r) => r.right > window.innerWidth + 1),
          };
        });
        const label = `${role}/${id}@${w}x${h}`;
        expect(m.hScroll, `${label} horizontal page scroll`).toBe(false);
        expect(m.negative, `${label} negative coordinates`).toBe(false);
        expect(m.pastViewport, `${label} extends past the viewport`).toBe(false);
        expect(m.hasAside, `${label} no right-hand aside`).toBe(false);
        expect(Math.abs(m.bandWidth - m.rootWidth), `${label} account band spans the column`).toBeLessThanOrEqual(1);
        if (m.rootWidth >= 960) {
          expect(m.navVertical, `${label} nav vertical`).toBe(true);
          expect(Math.round(m.navWidth), `${label} nav is 240px`).toBe(240);
          // nav labels never touch the content (at least 16px of gap)
          expect(m.contentLeft - m.navRect.right, `${label} gap between nav and content`).toBeGreaterThanOrEqual(16);
          expect(m.navItemsRight, `${label} nav items stay inside the nav`).toBeLessThanOrEqual(m.navRect.right + 1);
          // content fills the column: up to 880px, or the whole column for management tables
          const available = m.rootRight - m.contentLeft;
          const wide = ['users', 'adminareas', 'audit'].includes(id);
          const expected = wide ? available : Math.min(available, 880);
          expect(Math.abs(m.contentWidth - expected), `${label} content width ${m.contentWidth} vs ${expected}`).toBeLessThanOrEqual(2);
        } else {
          expect(m.navVertical, `${label} nav is a tab row`).toBe(false);
          expect(m.contentTop, `${label} content starts below the tab row`).toBeGreaterThanOrEqual(m.navItemsBottom - 1);
        }
      }
    }
  });
}

// ------------------------------------------------------------------ sections per role
for (const role of ROLES) {
  test(`${role}: sub-navigation lists exactly the right sections`, async ({ page }) => {
    await openSettings(page, role);
    const ids = await page.locator('[data-testid=settings-nav] button[data-section]').evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.section));
    expect(ids).toEqual(SECTIONS[role]);
  });
}

test('account band: identity, status, two-step status and the notice version', async ({ page }) => {
  await openSettings(page, 'admin');
  const band = page.getByTestId('settings-account-band');
  await expect(band).toContainText('Demo Administrator');
  await expect(band).toContainText('admin@demo.example');
  await expect(page.getByTestId('band-status')).toBeVisible();
  await expect(page.getByTestId('band-two-step')).toContainText('Two-step');
  await expect(page.getByTestId('band-notice')).toContainText('version');
  await page.getByTestId('band-notice').click();
  await expect(page.getByTestId('notice-modal')).toContainText('Notice version');
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('notice-modal')).toHaveCount(0);
});

test('account band: wraps to two rows under 960px', async ({ page }) => {
  await openSettings(page, 'msl');
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.waitForTimeout(200);
  const rows = await page.evaluate(() => {
    const band = document.querySelector('[data-testid=settings-account-band]')!;
    const tops = new Set(Array.from(band.querySelectorAll('p, span[data-testid], button')).map((e) => Math.round(e.getBoundingClientRect().top / 10)));
    return tops.size;
  });
  expect(rows).toBeGreaterThanOrEqual(2);
});

// ------------------------------------------------------------------ save bar and Save changes button
test('save bar: appears when the profile is dirty; Discard restores; Save persists', async ({ page }) => {
  await openSettings(page, 'msl');
  await gotoSection(page, 'profile');
  const input = page.locator('#profile-name');
  const original = await input.inputValue();
  await expect(page.getByTestId('save-bar')).toHaveCount(0);

  await input.fill(`${original} Edited`);
  await expect(page.getByTestId('save-bar')).toBeVisible();
  await expect(page.getByText('Unsaved changes')).toBeVisible();

  await page.getByTestId('save-bar-discard').click();
  await expect(page.getByTestId('save-bar')).toHaveCount(0);
  await expect(input).toHaveValue(original);

  await input.fill(`${original} Edited`);
  await page.getByTestId('save-bar-save').click();
  await expect(page.getByTestId('save-bar')).toHaveCount(0);
  await expect(page.getByTestId('sidebar-user')).toHaveText(`${original} Edited`);

  await input.fill(original);
  await page.getByTestId('profile-save').click();
  await expect(page.getByTestId('sidebar-user')).toHaveText(original);
});

test('save bar is sticky at the bottom of the content while the form is dirty', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 700 });
  await openSettings(page, 'msl');
  await gotoSection(page, 'profile');
  await page.locator('#profile-name').fill('Sticky Check');
  const bar = page.getByTestId('save-bar');
  await expect(bar).toBeVisible();
  const box = await bar.boundingBox();
  const main = await page.locator('#main-content').boundingBox();
  expect(box!.y + box!.height).toBeLessThanOrEqual(main!.y + main!.height + 1);
  expect(box!.y).toBeGreaterThan(main!.y);
});

function luminance([r, g, b]: number[]) {
  const f = (c: number) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function contrast(a: number[], b: number[]) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
const rgb = (s: string) => (s.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);

for (const mode of ['light', 'dark'] as Mode[]) {
  test(`Save changes button stays readable ${mode}: disabled (nothing changed) and enabled (dirty)`, async ({ page }) => {
    await openSettings(page, 'msl', mode);
    await gotoSection(page, 'profile');
    const button = page.getByTestId('profile-save');
    const read = () =>
      button.evaluate((el) => {
        const cs = getComputedStyle(el);
        return { color: cs.color, bg: cs.backgroundColor, opacity: cs.opacity, disabled: (el as HTMLButtonElement).disabled };
      });
    await page.waitForTimeout(400);
    const off = await read();
    expect(off.disabled).toBe(true);
    expect(off.opacity).toBe('1');
    expect(contrast(rgb(off.color), rgb(off.bg)), `disabled ${mode}`).toBeGreaterThanOrEqual(4.5);
    await page.locator('#profile-name').fill('Demo Physical MSL X');
    await page.waitForTimeout(400); // let the colour transition finish before reading computed styles
    const on = await read();
    expect(on.disabled).toBe(false);
    expect(on.opacity).toBe('1');
    expect(contrast(rgb(on.color), rgb(on.bg)), `enabled ${mode}`).toBeGreaterThanOrEqual(4.5);
    await page.getByTestId('save-bar-discard').click();
  });
}

test('profile: short name shows an inline error and blocks Save; read-only fields are locked', async ({ page }) => {
  await openSettings(page, 'msl');
  await gotoSection(page, 'profile');
  await page.locator('#profile-name').fill('A');
  await expect(page.locator('#profile-name-error')).toContainText('at least 2 characters');
  await expect(page.getByTestId('profile-save')).toBeDisabled();
  await expect(page.locator('#profile-email')).toHaveAttribute('readonly', '');
  await expect(page.getByText('Contact your administrator to change your email.')).toBeVisible();
  // more than two fields on the card
  expect(await page.locator('[data-testid=card-profile] input').count()).toBeGreaterThanOrEqual(4);
});

test('profile: two-column field grid from 1200px, one column below', async ({ page }) => {
  await openSettings(page, 'msl');
  await gotoSection(page, 'profile');
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.waitForTimeout(200);
  const two = await page.locator('.settings-fields').first().evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length);
  expect(two).toBe(2);
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.waitForTimeout(200);
  const one = await page.locator('.settings-fields').first().evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length);
  expect(one).toBe(1);
});

test('reply wording: save bar for a Physical MSL', async ({ page }) => {
  await openSettings(page, 'msl');
  await gotoSection(page, 'wording');
  const body = page.locator('#wording-body');
  await body.waitFor();
  const original = await body.inputValue();
  await body.fill(`${original}\nTEST-LINE`);
  await expect(page.getByTestId('save-bar')).toBeVisible();
  await page.getByTestId('save-bar-discard').click();
  await expect(body).toHaveValue(original);
  await expect(page.getByTestId('save-bar')).toHaveCount(0);
});

test('security: password rules, mismatch error, wrong current password', async ({ page }) => {
  await openSettings(page, 'msl');
  await gotoSection(page, 'security');
  await page.locator('#pw-current').fill('not-the-password');
  await page.locator('#pw-new').fill('short');
  await expect(page.getByText('Strength:')).toBeVisible();
  await page.locator('#pw-confirm').fill('different');
  await page.getByTestId('password-save').click();
  await expect(page.locator('#pw-new-error')).toBeVisible();
  await expect(page.locator('#pw-confirm-error')).toContainText('do not match');

  await page.locator('#pw-new').fill('A-Brand-New-Passw0rd!');
  await page.locator('#pw-confirm').fill('A-Brand-New-Passw0rd!');
  await page.getByTestId('password-save').click();
  await expect(page.locator('#pw-current-error')).toContainText('Current password is incorrect');
  await expect(page.getByTestId('card-two-step')).toBeVisible();
});

test('preferences: Light, Dark and System', async ({ page }) => {
  await openSettings(page, 'doctor');
  await gotoSection(page, 'preferences');
  await page.getByTestId('theme-dark').click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect(page.getByTestId('theme-dark')).toHaveAttribute('aria-checked', 'true');
  await page.getByTestId('theme-light').click();
  await expect(page.locator('html')).not.toHaveClass(/dark/);
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.getByTestId('theme-system').click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await page.emulateMedia({ colorScheme: 'light' });
});

test('doctor: therapeutic areas with Make default, consent toggles and withdraw', async ({ page }) => {
  await openSettings(page, 'doctor');
  await gotoSection(page, 'areas');
  const items = page.locator('[data-testid=card-areas] li');
  await expect(items).toHaveCount(2);
  await expect(items.first()).toContainText('Default');
  await page.getByRole('button', { name: /Make .* your default area/ }).click();
  await expect(items.nth(1)).toContainText('Default');
  await page.getByRole('button', { name: /Make .* your default area/ }).click();
  await expect(items.first()).toContainText('Default');

  await gotoSection(page, 'consent');
  const wa = page.getByRole('switch', { name: 'WhatsApp messages consent' });
  await expect(wa).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('consent-whatsapp_messaging')).toContainText('notice version');
  await page.getByTestId('withdraw-consent').click();
  await page.getByRole('dialog').getByRole('button', { name: 'Withdraw' }).click();
  await expect(wa).toHaveAttribute('aria-checked', 'false');
  await expect(page.getByRole('switch', { name: 'Email consent' })).toHaveAttribute('aria-checked', 'false');
  await expect(page.getByTestId('withdraw-consent')).toBeDisabled();
  await wa.click();
  await expect(wa).toHaveAttribute('aria-checked', 'true');
  await page.getByRole('switch', { name: 'Email consent' }).click();
  await expect(page.getByRole('switch', { name: 'Email consent' })).toHaveAttribute('aria-checked', 'true');

  await gotoSection(page, 'registration');
  await expect(page.getByTestId('card-registration')).toContainText('Verified');
});

// ------------------------------------------------------------------ administrator sections
test('admin: users table with search, role filter, invite and reset password', async ({ page }) => {
  await openSettings(page, 'admin');
  await gotoSection(page, 'users');
  const table = page.getByTestId('card-users');
  await expect(table.getByRole('row')).not.toHaveCount(1);
  await expect(table).toContainText('doctor@demo.example');
  await page.locator('#users-search').fill('msl@');
  await expect(table.getByRole('row')).toHaveCount(2); // header + the demo Physical MSL
  await page.locator('#users-search').fill('');
  await page.locator('#users-role').selectOption('doctor');
  await expect(table).not.toContainText('msl@demo.example');
  await expect(table).toContainText('doctor@demo.example');

  const stamp = Date.now();
  const inviteeName = `Test Invitee ${stamp}`;
  await page.locator('#invite-name').fill(inviteeName);
  await page.locator('#invite-email').fill(`invitee${stamp}@example.com`);
  await page.getByRole('button', { name: 'Save person' }).click();
  await expect(page.getByTestId('secret-modal')).toContainText('Temporary password');
  await page.getByRole('button', { name: 'Done' }).click();

  await page.locator('#users-role').selectOption('all');
  // reset the person invited above, never a demo account (that would change its password for later tests)
  await page.getByRole('button', { name: `Reset password for ${inviteeName}` }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Reset password' }).click();
  await expect(page.getByTestId('secret-modal')).toContainText('Temporary password');
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(page.getByText('Showing').first()).toBeVisible();
});

test('admin: IKM status is calm and never shows raw errors; Test connection works', async ({ page }) => {
  await openSettings(page, 'admin');
  await gotoSection(page, 'ikm');
  const card = page.getByTestId('card-ikm');
  await expect(card.getByText(/Connected|Not connected/)).toBeVisible({ timeout: 60_000 });
  const text = (await card.innerText()).toLowerCase();
  for (const raw of ['bad gateway', 'traceback', 'sslerror', 'errno']) expect(text).not.toContain(raw);
  await page.getByTestId('ikm-test').click();
  await expect(page.getByTestId('ikm-test')).toBeEnabled({ timeout: 60_000 });
});

test('admin: notice, integrations, audit log and environment banner', async ({ page }) => {
  await openSettings(page, 'admin');
  await gotoSection(page, 'notice');
  await expect(page.getByTestId('card-notice')).toContainText('version');
  await gotoSection(page, 'integrations');
  await expect(page.getByTestId('card-integrations')).toContainText('Credentials present');
  await gotoSection(page, 'audit');
  await expect(page.getByTestId('card-audit').getByRole('row').nth(1)).toBeVisible();
  await page.locator('#audit-action').fill('login');
  await expect(page.getByTestId('card-audit')).toContainText('login');
  await gotoSection(page, 'environment');
  await expect(page.getByTestId('demo-banner')).toContainText('Demo mode is on');
  await expect(page.getByTestId('card-environment')).toContainText('Confidence threshold');
});

test('admin: teams tools are kept under Users and roles', async ({ page }) => {
  await openSettings(page, 'admin');
  await gotoSection(page, 'users');
  await expect(page.getByText('New MSL team')).toBeVisible();
});

// ------------------------------------------------------------------ keyboard
test('keyboard: arrow keys move through the sub-navigation and Enter selects', async ({ page }) => {
  await openSettings(page, 'doctor');
  await page.getByTestId('settings-nav-profile').focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByTestId('settings-nav-security')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('settings-nav-security')).toHaveAttribute('aria-current', 'page');
  await expect(page.getByTestId('card-password')).toBeVisible();
  await page.keyboard.press('End');
  await expect(page.getByTestId('settings-nav-activity')).toBeFocused();
  await page.keyboard.press('Home');
  await expect(page.getByTestId('settings-nav-profile')).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(page.getByTestId('settings-nav-activity')).toBeFocused();
});

// ------------------------------------------------------------------ axe, every section, both modes
for (const role of ROLES) {
  for (const mode of ['light', 'dark'] as Mode[]) {
    test(`axe ${role} ${mode}: no contrast or serious violations in any section`, async ({ page }) => {
      test.setTimeout(300_000);
      await page.setViewportSize({ width: 1440, height: 900 });
      await openSettings(page, role, mode);
      for (const id of SECTIONS[role]) {
        await gotoSection(page, id);
        await dismissDialogs(page);
        const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
        const blocking = results.violations.filter((v) => v.id === 'color-contrast' || v.impact === 'serious' || v.impact === 'critical');
        expect(
          blocking.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`),
          `${role}/${id}/${mode} axe`,
        ).toEqual([]);
      }
    });
  }
}

// ------------------------------------------------------------------ screenshots (looked at by hand afterwards)
for (const role of ROLES) {
  for (const mode of ['light', 'dark'] as Mode[]) {
    test(`screenshots ${role} ${mode}`, async ({ page }) => {
      test.setTimeout(600_000);
      await page.setViewportSize({ width: 1440, height: 900 });
      await openSettings(page, role, mode);
      for (const id of SECTIONS[role]) {
        for (const { w, h } of SHOT_VIEWPORTS) {
          await page.setViewportSize({ width: w, height: h });
          await gotoSection(page, id);
          await page.waitForTimeout(150);
          await page.screenshot({ path: shot(role, id, mode, w, h) });
        }
      }
    });
  }
}
