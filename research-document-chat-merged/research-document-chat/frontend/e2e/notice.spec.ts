import { expect, test, type Page } from '@playwright/test';
import { signIn, type Mode } from './helpers';

const SHOTS = '../docs/screenshots/fixAB';
const SIZES = [
  { w: 1440, h: 900 },
  { w: 1920, h: 1080 },
  { w: 768, h: 1024 },
  { w: 360, h: 800 },
] as const;
const ROUTING = 'Your question will be reviewed by a Physical MSL.';
const DISCLAIMER = 'AI-generated from approved sources. Not medical advice. Report adverse events to [PHARMACOVIGILANCE CONTACT].';

async function openChat(page: Page, mode: Mode = 'light', size: { w: number; h: number } = { w: 1440, h: 900 }) {
  await page.setViewportSize({ width: size.w, height: size.h });
  await signIn(page, 'doctor', mode);
  await expect(page.getByTestId('chat-thread')).toBeVisible();
}

/** Replies to web messages with a fixed greeting, so repeated runs never hit the real per-minute rate limit. */
async function mockGreeting(page: Page) {
  const payload = {
    type: 'result',
    message_id: 'msg_mock',
    correlation_id: 'corr_mock',
    conversation_id: 'conv_mock',
    channel: 'web',
    status: 'answered',
    decision: 'respond',
    answer: 'Good morning, Dr. Demo Doctor. I can help with scientific questions about Retina and Vitreous. What would you like to know?',
    overall_score: 0,
    threshold: 0.9,
    claims: [],
    reason_codes: [],
    intent: 'greeting',
    message_type: 'assistant_note',
    retrieval_ran: false,
    llm_called: false,
    actions: [],
    metrics: null,
  };
  await page.route('**/channels/web/messages', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: `data: ${JSON.stringify(payload)}\n\n` }),
  );
}

async function send(page: Page, text: string) {
  await page.getByLabel('Your question').fill(text);
  await page.getByRole('button', { name: 'Send question' }).click();
}

test.describe('A. compliance notice shows once, in the thread', () => {
  test('1. a new conversation shows the notice once, as the first item inside the thread', async ({ page }) => {
    await openChat(page);
    const thread = page.getByTestId('chat-thread');
    const card = thread.getByTestId('notice-card');
    await expect(card).toHaveCount(1);
    await expect(card).toBeVisible();
    await expect(card).toContainText('Notice version');
    await expect(card).toContainText('Digital MSL provides scientific and medical information');
    // first item of the thread, not a banner above it
    expect(await thread.evaluate((el) => (el.firstElementChild as HTMLElement | null)?.getAttribute('data-testid'))).toBe('notice-card');
    expect(await page.locator('[aria-label="Compliance notice"]').count()).toBe(1);
    // it takes a card's worth of the thread, not a third of the panel
    const box = (await card.boundingBox())!;
    const panel = (await page.getByTestId('chat-panel').boundingBox())!;
    expect(box.height).toBeLessThan(panel.height / 3);
    await expect(page.getByTestId('notice-gotit')).toBeVisible();
  });

  test('2. after the first message the notice is gone; Notice link and the one-line disclaimer stay', async ({ page }) => {
    await mockGreeting(page);
    await openChat(page);
    await send(page, 'hi');
    await expect(page.getByTestId('chat-thread')).toContainText('Dr. Demo Doctor', { timeout: 30_000 });
    await expect(page.getByTestId('notice-card')).toHaveCount(0);
    await expect(page.getByTestId('notice-link')).toBeVisible();
    await expect(page.getByTestId('composer-disclaimer')).toHaveText(DISCLAIMER);
  });

  test('3. Got it removes the card; a reload does not show it again', async ({ page }) => {
    await openChat(page);
    await page.getByTestId('notice-gotit').click();
    await expect(page.getByTestId('notice-card')).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId('chat-thread')).toBeVisible();
    await expect(page.getByTestId('chat-area-name')).toBeVisible();
    await expect(page.getByTestId('notice-card')).toHaveCount(0);
    await expect(page.getByTestId('notice-link')).toBeVisible();
  });

  test('4. a new notice version is shown again, and only until it is acknowledged', async ({ page }) => {
    await openChat(page);
    await page.getByTestId('notice-gotit').click();
    await expect(page.getByTestId('notice-card')).toHaveCount(0);
    await page.route('**/digital-msl/compliance/notice', (route) =>
      route.fulfill({ json: { version: '2026-12-v9', text: 'Digital MSL provides scientific and medical information (test wording). [MLR TO APPROVE]' } }),
    );
    await page.reload();
    const card = page.getByTestId('notice-card');
    await expect(card).toBeVisible();
    await expect(card).toContainText('Notice version 2026-12-v9');
    await page.getByTestId('notice-gotit').click();
    await expect(card).toHaveCount(0);
    await page.reload();
    await expect(page.getByTestId('chat-area-name')).toBeVisible();
    await expect(page.getByTestId('notice-card')).toHaveCount(0);
  });

  test('5. the Notice link opens the full text with the version in a modal; Esc closes it', async ({ page }) => {
    await openChat(page);
    await page.getByTestId('notice-gotit').click();
    await page.getByTestId('notice-link').click();
    const modal = page.getByTestId('notice-modal');
    await expect(modal).toBeVisible();
    await expect(modal).toContainText('Notice version 2026-11-v1');
    await expect(modal).toContainText('[MLR TO APPROVE]');
    await page.keyboard.press('Escape');
    await expect(modal).toHaveCount(0);
  });

  test('6. the routing note is not a permanent banner; its meaning lives in the notice and in the escalation message', async ({ page }) => {
    await openChat(page);
    await expect(page.getByTestId('routed-to-msl')).toHaveCount(0);
    await expect(page.getByText(ROUTING)).toHaveCount(1); // only inside the first-conversation notice
    await expect(page.getByTestId('notice-card')).toContainText(ROUTING);
    await send(page, 'What is the effect of aflibercept on central subfield thickness in diabetic macular edema?');
    const escalation = page.getByTestId('msg-escalation');
    await expect(escalation).toBeVisible({ timeout: 60_000 });
    await expect(escalation).toContainText('Physical MSL');
    await expect(page.getByTestId('notice-card')).toHaveCount(0);
  });

  test('7. the area header is one line: area name, Searching N libraries, Notice link', async ({ page }) => {
    await openChat(page);
    const header = page.getByTestId('chat-area-header');
    await expect(header).toContainText('Retina and Vitreous');
    await expect(header).toContainText(/Searching \d+ librar/);
    const name = (await page.getByTestId('chat-area-name').boundingBox())!;
    const link = (await page.getByTestId('notice-link').boundingBox())!;
    const hb = (await header.boundingBox())!;
    expect(Math.abs(name.y + name.height / 2 - (link.y + link.height / 2))).toBeLessThan(12);
    expect(hb.height).toBeLessThan(70);
  });
});

for (const mode of ['light', 'dark'] as const) {
  test(`A screenshots ${mode}: new conversation, after Got it, Notice modal`, async ({ browser }) => {
    test.setTimeout(240_000);
    for (const size of SIZES) {
      const context = await browser.newContext({ viewport: { width: size.w, height: size.h }, serviceWorkers: 'block' });
      const page = await context.newPage();
      await mockGreeting(page);
      await openChat(page, mode, size);
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${SHOTS}/chat-new-conversation-${mode}-${size.w}x${size.h}.png` });
      await page.getByTestId('notice-gotit').click();
      await send(page, 'hi');
      await expect(page.getByTestId('chat-thread')).toContainText('Dr. Demo Doctor', { timeout: 30_000 });
      await page.screenshot({ path: `${SHOTS}/chat-after-first-message-${mode}-${size.w}x${size.h}.png` });
      await page.getByTestId('notice-link').click();
      await expect(page.getByTestId('notice-modal')).toBeVisible();
      await page.screenshot({ path: `${SHOTS}/chat-notice-modal-${mode}-${size.w}x${size.h}.png` });
      await context.close();
    }
  });
}
