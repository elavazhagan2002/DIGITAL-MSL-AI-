import type { Page } from '@playwright/test';

export type Role = 'doctor' | 'msl' | 'admin';
export type Mode = 'light' | 'dark';

export const VIEWPORTS = [
  { w: 1920, h: 1080 },
  { w: 1440, h: 900 },
  { w: 1366, h: 768 },
  { w: 1024, h: 768 },
  { w: 768, h: 1024 },
  { w: 360, h: 800 },
] as const;

/** The four sizes used for the per-screen screenshots. */
export const SHOT_VIEWPORTS = [
  { w: 1440, h: 900 },
  { w: 1024, h: 768 },
  { w: 768, h: 1024 },
  { w: 360, h: 800 },
] as const;

export const SCREENS: Record<Role, string[]> = {
  doctor: ['chat', 'whatsapp-demo', 'settings'],
  msl: ['cases', 'doctors', 'literature', 'settings'],
  admin: ['home', 'doctors', 'whatsapp-demo', 'conversations', 'settings'],
};

const ROLE_BUTTON: Record<Role, string> = { doctor: 'Doctor', msl: 'Physical MSL', admin: 'Administrator' };

/** Sign in through the real login screen (demo mode fills the credentials when a role is chosen). */
export async function signIn(page: Page, role: Role, mode: Mode = 'light') {
  await page.addInitScript((m) => {
    try {
      if (localStorage.getItem('dmsl_theme') === null) localStorage.setItem('dmsl_theme', m);
    } catch {
      /* ignore */
    }
  }, mode);
  await page.goto('/');
  await page.getByRole('button', { name: ROLE_BUTTON[role], exact: false }).first().click();
  await page.getByRole('button', { name: new RegExp(`Continue as ${ROLE_BUTTON[role]}`) }).click();
  await page.getByTestId('page-title').waitFor();
  await dismissDialogs(page);
}

/** Welcome pop-ups (Physical MSL queue summary) cover the screen until dismissed. */
export async function dismissDialogs(page: Page) {
  for (let i = 0; i < 3; i += 1) {
    const dialog = page.getByRole('dialog');
    if (!(await dialog.first().isVisible({ timeout: 1500 }).catch(() => false))) return;
    await dialog.getByRole('button').last().click();
    await page.waitForTimeout(150);
  }
}

/** Open a screen from the sidebar (opening the drawer first on narrow viewports). */
export async function openScreen(page: Page, id: string) {
  const item = page.getByTestId(`nav-${id}`);
  if (!(await item.isVisible())) {
    await page.getByRole('button', { name: 'Open menu' }).click();
  }
  await item.click();
  await page.waitForTimeout(250);
  await dismissDialogs(page);
}

export function shot(role: Role, screen: string, mode: Mode, w: number, h: number) {
  return `../docs/screenshots/shell/${role}-${screen}-${mode}-${w}x${h}.png`;
}
