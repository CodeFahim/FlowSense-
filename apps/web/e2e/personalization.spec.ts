import { expect, test, type Page } from "@playwright/test";
import { approve, login, password, resetTo } from "./helpers";

test.beforeEach(async ({ page }) => {
  test.skip(!password, "Set FLOWSENSE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  await login(page);
});

const html = (page: Page) => page.locator("html");

test("welcome step: the display currency is optional, never preset, and persists", async ({ page }) => {
  await resetTo(page, "Standard rescue");
  // Start from "no display currency chosen" (an earlier test may have saved one for this user).
  await page.evaluate(async () => {
    const me = await fetch("/api/auth/me").then((r) => r.json());
    await fetch("/api/me/preferences", { method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token }, body: JSON.stringify({ clear: ["display_currency"] }) });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Run the welcome steps again" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByRole("heading", { name: "Show approximate amounts in another currency?" })).toBeVisible();
  const display = page.getByRole("combobox");
  await expect(display).toHaveValue("");
  await display.click();
  await display.fill("bdt");
  await page.getByRole("option", { name: /^BDT/ }).click();
  await page.getByRole("button", { name: "Use BDT" }).click();
  await expect(page).toHaveURL(/\/$/); // the business currency was already confirmed by the reset

  await page.reload();
  await expect(page).toHaveURL(/\/$/);
  const prefs = await page.evaluate(() => fetch("/api/me/preferences").then((r) => r.json()));
  expect(prefs).toMatchObject({ display_currency: "BDT", onboarded: true });
  // Stored amounts never change: the books stay in USD.
  const me = await page.evaluate(() => fetch("/api/auth/me").then((r) => r.json()));
  expect(me.business.currency).toBe("USD");
});

test("theme and accent apply immediately, survive a reload, and can be reset", async ({ page }) => {
  await page.goto("/settings");
  await page.getByRole("radio", { name: "Dark" }).click();
  await expect(html(page)).toHaveAttribute("data-theme", "dark");
  await page.getByRole("radio", { name: "Violet" }).click();
  await expect(html(page)).toHaveAttribute("data-accent", "violet");

  await page.reload();
  await expect(html(page)).toHaveAttribute("data-theme", "dark");
  await expect(html(page)).toHaveAttribute("data-accent", "violet");
  // Every main page renders in dark mode with the chosen accent.
  for (const path of ["/", "/assistant", "/rescue", "/money-in", "/approvals", "/activity"]) {
    await page.goto(path);
    await expect(html(page)).toHaveAttribute("data-theme", "dark");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  }

  await page.goto("/settings");
  await page.getByRole("radio", { name: "Light" }).click();
  await expect(html(page)).toHaveAttribute("data-theme", "light");
  for (const accent of ["Teal", "Blue", "Emerald", "Amber"]) {
    await page.getByRole("radio", { name: accent }).click();
    await expect(html(page)).toHaveAttribute("data-accent", accent.toLowerCase());
  }
  await page.getByRole("button", { name: "Reset appearance" }).click();
  await expect(html(page)).toHaveAttribute("data-accent", "teal");
  const prefs = await page.evaluate(() => fetch("/api/me/preferences").then((r) => r.json()));
  expect(prefs).toMatchObject({ theme: "system", accent: "teal" });
});

test("currency converter: demo, manual and missing-rate states are labelled; nothing is recorded", async ({ page }) => {
  const before = await page.evaluate(() => fetch("/api/ledger").then((r) => r.json()));
  await page.getByRole("button", { name: "Open currency converter" }).click();
  const drawer = page.getByRole("dialog", { name: "Currency converter" });
  await expect(drawer).toBeVisible();
  await drawer.getByLabel("Amount").fill("100");
  const pick = async (label: string, code: string) => {
    const box = drawer.getByLabel(label, { exact: true });
    await box.click();
    await box.fill(code);
    await page.getByRole("option", { name: new RegExp(`^${code}`) }).click();
  };
  await pick("From", "USD");
  await pick("To", "BDT");

  // Online rates: either a labelled provider rate (local) or "no automatic rate" (CI has rates off). Never a guess.
  await drawer.getByRole("button", { name: "Convert" }).click();
  await expect(drawer.getByText(/No automatic rate|Latest published rate|Cached rate|Out-of-date rate/).first()).toBeVisible();

  await drawer.getByRole("radio", { name: "Demo" }).click();
  await drawer.getByRole("button", { name: "Convert" }).click();
  await expect(drawer.getByText("৳11,818.18")).toBeVisible();
  await expect(drawer.getByText("Demo rate (not market data)", { exact: true })).toBeVisible();
  await expect(drawer.getByText(/not market data/).first()).toBeVisible();

  await drawer.getByRole("radio", { name: "Manual" }).click();
  await drawer.getByLabel(/1 USD equals how many BDT/).fill("117.5");
  await drawer.getByRole("button", { name: "Convert" }).click();
  await expect(drawer.getByText("৳11,750.00")).toBeVisible();
  await expect(drawer.getByText("Manual rate (your figure)", { exact: true })).toBeVisible();

  await drawer.getByRole("button", { name: "Swap currencies" }).click();
  await expect(drawer.getByLabel("From", { exact: true })).toHaveValue(/^BDT/);
  // Zero-decimal currency: JPY has no minor units.
  await pick("To", "JPY");
  await drawer.getByRole("radio", { name: "Demo" }).click();
  await drawer.getByLabel("Amount").fill("130");
  await drawer.getByRole("button", { name: "Convert" }).click();
  await expect(drawer.getByText("¥160 JPY")).toBeVisible(); // two currencies on screen: the shared "¥" is labelled

  const after = await page.evaluate(() => fetch("/api/ledger").then((r) => r.json()));
  expect(after).toEqual(before);
});

test("isolated USD fixture: full rescue in USD; display preferences never touch the ledger", async ({ page }) => {
  await resetTo(page, "Standard rescue");
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("You need an additional USD 40,000.00");
  await expect(page.locator("p", { hasText: /^\$50,000\.00$/ }).first()).toBeVisible();

  await page.goto("/approvals");
  await approve(page, "Payment request to");
  await page.getByRole("button", { name: "Send request (simulated)" }).click();
  await page.getByRole("button", { name: "Customer pays $40,000.00" }).click();
  await expect(page.getByText("outstanding now USD 20,000.00")).toBeVisible();
  await page.getByRole("tab", { name: /^Needs approval/ }).click();
  await approve(page, "Payroll payment to");
  await page.getByRole("button", { name: "Submit to sandbox bank" }).dblclick();
  await expect(page.getByText(/USD 80,000\.00 settled to SBX-PAYROLL-0001/)).toBeVisible();

  const ledger = await page.evaluate(() => fetch("/api/ledger").then((r) => r.json()));
  // Changing the display currency is presentation only.
  await page.evaluate(async () => {
    const me = await fetch("/api/auth/me").then((r) => r.json());
    await fetch("/api/me/preferences", { method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token }, body: JSON.stringify({ display_currency: "EUR" }) });
  });
  expect(await page.evaluate(() => fetch("/api/ledger").then((r) => r.json()))).toEqual(ledger);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("The rescue is complete");
  await expect(page.locator("p", { hasText: /^\$10,000\.00$/ }).first()).toBeVisible();
  await page.evaluate(async () => {
    const me = await fetch("/api/auth/me").then((r) => r.json());
    await fetch("/api/me/preferences", { method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token }, body: JSON.stringify({ display_currency: "USD" }) });
  });
});

test.describe("mobile layout", () => {
  test.use({ viewport: { width: 360, height: 780 }, isMobile: true, hasTouch: true });

  test("navigation drawer works and no page scrolls sideways", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Open navigation" }).click();
    const nav = page.getByRole("dialog", { name: "Navigation" });
    for (const name of ["Dashboard", "Money", "Customers", "AI Assistant"]) await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
    await nav.getByText("More", { exact: true }).click();
    await nav.getByRole("link", { name: "Approvals" }).click();
    await expect(page).toHaveURL(/\/approvals$/);
    await expect(nav).toBeHidden();
    for (const path of ["/", "/assistant", "/money", "/money/forecast", "/customers", "/rescue", "/money-in", "/money-out", "/approvals", "/activity", "/settings", "/help", "/setup", "/demo"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
    }
    // The chat composer stays usable.
    await page.goto("/assistant");
    await expect(page.getByLabel("Message the assistant")).toBeVisible();
  });
});
