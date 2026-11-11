/**
 * BEFORE screenshots for the UI migration baseline (docs/ui-baseline/before/).
 * One file per screen, role and mode at 1440x900. Skipped screens are logged with the reason.
 * Run: npx playwright test e2e/baseline-shots.spec.ts   (backend on 8098 in demo mode)
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { test, type Page } from '@playwright/test';
import { SCREENS, dismissDialogs, openScreen, signIn, type Mode, type Role } from './helpers';

const OUT = '../docs/ui-baseline/before';
const log: Array<{ file: string; status: 'captured' | 'skipped'; reason?: string }> = [];

mkdirSync(OUT, { recursive: true });

async function snap(page: Page, name: string, mode: Mode) {
  const file = `${name}-${mode}-1440x900.png`;
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/${file}`, fullPage: false });
  log.push({ file, status: 'captured' });
}

function skip(name: string, mode: Mode, reason: string) {
  log.push({ file: `${name}-${mode}-1440x900.png`, status: 'skipped', reason });
}

async function setTheme(page: Page, mode: Mode) {
  await page.addInitScript((m) => {
    try {
      localStorage.setItem('dmsl_theme', m);
      localStorage.setItem('dmsl_login_theme', m);
    } catch {
      /* ignore */
    }
  }, mode);
}

test.afterAll(() => {
  writeFileSync('../docs/ui-baseline/before/_capture-log.json', JSON.stringify(log, null, 1));
});

for (const mode of ['light', 'dark'] as Mode[]) {
  test(`public screens ${mode}`, async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setTheme(page, mode);
    await page.goto('/');
    await page.getByRole('button', { name: /Doctor/ }).first().waitFor();
    await snap(page, 'public-login', mode);

    try {
      await page.getByRole('button', { name: /Doctor\? Register here/ }).click();
      await page.locator('#reg-name').waitFor();
      await snap(page, 'public-register-1', mode);
      await page.locator('#reg-name').fill('Dr. Baseline Tester');
      await page.locator('#reg-email').fill(`baseline.${mode}@example.org`);
      await page.locator('#reg-mobile').fill('9876543210');
      await page.locator('#reg-password').fill('Str0ng!Passw0rd#1');
      await page.locator('#reg-confirm').fill('Str0ng!Passw0rd#1');
      await page.getByRole('button', { name: 'Continue' }).click();
      await page.locator('#reg-number').waitFor();
      await snap(page, 'public-register-2', mode);
      await page.locator('#reg-number').fill('TN-12345');
      await page.locator('#reg-year').fill('2012');
      await page.locator('#reg-council').selectOption({ index: 1 });
      await page.locator('#reg-qual').selectOption({ index: 1 });
      await page.locator('#reg-hqual').selectOption({ index: 1 });
      await page.locator('#reg-speciality').selectOption({ index: 1 }).catch(() => undefined);
      await page.locator('#reg-institution').fill('City Eye Hospital');
      await page.locator('#reg-city').fill('Chennai');
      await page.locator('#reg-state').fill('Tamil Nadu');
      await page.locator('#reg-pin').fill('600001');
      await page.getByRole('button', { name: 'Continue' }).click();
      await page.getByText(/Therapeutic areas/).first().waitFor();
      await snap(page, 'public-register-3', mode);
      const firstArea = page.locator('fieldset input[type=checkbox]:not([disabled])').first();
      if (await firstArea.count()) await firstArea.check();
      await page.getByRole('button', { name: 'Continue' }).click();
      await page.getByText(/Consent/i).first().waitFor();
      await snap(page, 'public-register-4', mode);
    } catch (err) {
      skip('public-register-steps', mode, `registration steps could not all be reached: ${String(err).slice(0, 160)}`);
    }
  });
}

const ROLES: Role[] = ['doctor', 'msl', 'admin'];

for (const mode of ['light', 'dark'] as Mode[]) {
  for (const role of ROLES) {
    test(`signed-in ${role} ${mode}`, async ({ page }) => {
      test.setTimeout(300_000);
      await page.setViewportSize({ width: 1440, height: 900 });
      await signIn(page, role, mode);

      for (const screen of SCREENS[role]) {
        try {
          await openScreen(page, screen);
          await snap(page, `${role}-${screen}`, mode);
        } catch (err) {
          skip(`${role}-${screen}`, mode, String(err).slice(0, 160));
        }
      }

      if (role === 'doctor') {
        try {
          await openScreen(page, 'chat');
          await page.getByLabel('Your question').fill('hi');
          await page.getByRole('button', { name: 'Send question' }).click();
          await page.locator('[data-testid="msg-assistant-note"]').first().waitFor({ timeout: 30_000 });
          await snap(page, 'doctor-chat-notes', mode);
        } catch (err) {
          skip('doctor-chat-notes', mode, String(err).slice(0, 160));
        }
      }

      if (role === 'msl' || role === 'admin') {
        try {
          await openScreen(page, 'doctors');
          const row = page.locator('table tbody tr').first();
          if (await row.count()) {
            await row.click();
            await page.getByRole('dialog').first().waitFor({ timeout: 5_000 });
            await snap(page, `${role}-doctors-drawer`, mode);
          } else {
            skip(`${role}-doctors-drawer`, mode, 'no doctor rows to open');
          }
        } catch (err) {
          skip(`${role}-doctors-drawer`, mode, String(err).slice(0, 160));
        }
      }

      if (role === 'admin') {
        await openScreen(page, 'settings');
        for (const [tab, name] of [
          ['people', 'People'],
          ['areas', 'Therapeutic areas'],
          ['scope', 'Scope policy'],
          ['account', 'Account'],
        ] as const) {
          try {
            await page.getByRole('button', { name, exact: true }).click();
            await snap(page, `admin-settings-${tab}`, mode);
          } catch (err) {
            skip(`admin-settings-${tab}`, mode, String(err).slice(0, 160));
          }
        }
      }

      if (role === 'msl') {
        try {
          await openScreen(page, 'settings');
          await snap(page, 'msl-settings-account', mode);
        } catch (err) {
          skip('msl-settings-account', mode, String(err).slice(0, 160));
        }
      }
    });
  }
}

// The Physical MSL welcome dialog is dismissed by signIn(), so capture it separately.
for (const mode of ['light', 'dark'] as Mode[]) {
  test(`msl queue welcome dialog ${mode}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await setTheme(page, mode);
    await page.goto('/');
    await page.getByRole('button', { name: /Physical MSL/ }).first().click();
    await page.getByRole('button', { name: /Continue as Physical MSL/ }).click();
    try {
      await page.getByRole('dialog').first().waitFor({ timeout: 8_000 });
      await snap(page, 'msl-queue-welcome', mode);
    } catch {
      skip('msl-queue-welcome', mode, 'dialog did not appear');
    }
    await dismissDialogs(page);
  });
}
