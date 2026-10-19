import { expect, type Page } from "@playwright/test";

export const password = process.env.LIFELINE_DEMO_PASSWORD;

/** Signs in as the owner (or another demo user) and finishes the first-run welcome steps if they appear. */
export async function login(page: Page, email = "demo@lifeline.example") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/(welcome)?$/);
  if (page.url().endsWith("/welcome")) await skipWelcome(page);
  await expect(page).toHaveURL(/\/$/);
}

/** Completes the welcome flow: keep the accounting currency for display, confirm the books' currency. */
export async function skipWelcome(page: Page) {
  // Click whichever step's button is on screen. Reading the heading first was flaky in CI (#51, #53): during a
  // step's exit animation the old heading is still shown, and the helper then waited for the wrong button.
  const next = page.getByRole("button", { name: /^No, keep |Try the demo|^Confirm / });
  for (let i = 0; i < 6 && page.url().endsWith("/welcome"); i++) {
    await expect(next.first()).toBeEnabled({ timeout: 20_000 });
    await next.first().click({ timeout: 10_000 }).catch(() => undefined);
    await page.waitForTimeout(600);
  }
  await page.waitForURL(/\/$/);
}

/** Owner-only demo reset to the scenario card with this title (archives, never deletes). */
export async function resetTo(page: Page, title: string) {
  await page.goto("/demo");
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: title, exact: true }) });
  await card.getByRole("button", { name: "Reset to this scenario" }).click();
  await page.getByLabel("Type RESET to confirm").fill("RESET");
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page.getByText(`Reset to '${title}'`)).toBeVisible();
}

export const item = (page: Page, text: string | RegExp) => page.locator("li").filter({ hasText: text }).first();

/** Opens an Approvals tab by its label (the count is part of the accessible name). */
export async function tab(page: Page, label: string) {
  await page.getByRole("tab", { name: new RegExp(`^${label}`) }).click();
}

/** Approves the queue item containing `text` through the review dialog. */
export async function approve(page: Page, text: string | RegExp) {
  await item(page, text).getByRole("button", { name: "Review & approve" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: /^Approve \S/ }).click();
}
