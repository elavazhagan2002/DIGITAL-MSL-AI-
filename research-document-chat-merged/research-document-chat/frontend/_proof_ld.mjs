// Temporary: sign in as the demo Physical MSL on the user's dev server and capture Load Documents.
import { chromium } from '@playwright/test';

const out = process.argv[2] || '../docs/ui-baseline';
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, serviceWorkers: 'block' });
const page = await context.newPage();
await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
await page.locator('#login-email').waitFor({ timeout: 15000 });
await page.getByRole('button', { name: /Physical MSL/ }).first().click();
await page.getByRole('button', { name: /Continue as/ }).click();
await page.getByTestId('page-title').waitFor({ timeout: 30000 });
await page.getByRole('button', { name: /Go to My Queue/ }).click().catch(() => {});
await page.getByTestId('nav-literature').click();
await page.waitForTimeout(30000);
console.log('title:', await page.getByTestId('page-title').innerText());
const body = (await page.locator('main').innerText()).replace(/\s+/g, ' ');
console.log('page text (first 900):', body.slice(0, 900));
for (const label of [/PubMed/i, /ClinicalTrials/i, /Import from literature/i, /Bad Gateway/i, /can't load/i]) {
  console.log(String(label), 'visible matches:', await page.getByText(label).count());
}
await page.screenshot({ path: `${out}/load-documents-live-1440x900.png` });
await browser.close();
