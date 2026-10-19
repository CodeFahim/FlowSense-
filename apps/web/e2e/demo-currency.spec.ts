import { expect, test } from "@playwright/test";
import { approve, login, resetTo, tab } from "./helpers";

// The demo uses the currency the user picks: a private sample is rebuilt with its books in that currency.
// The shared demo keeps its books and shows labelled approximate amounts instead.

const PW = "playwright demo currency passphrase 5";

test("private sample: picking JPY rebuilds the demo in yen and the full rescue runs in yen", async ({ page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now();
  await page.goto("/signup");
  await page.getByLabel("Email address").fill(`pw-jpy-${stamp}@example.test`);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Full name").fill("Yen Tester");
  await page.getByLabel("Username").fill(`jpy_${stamp}`);
  await page.getByLabel("Password", { exact: true }).fill(PW);
  await page.getByLabel("Confirm password").fill(PW);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
  await page.getByRole("button", { name: /Try the demo/ }).click();
  await expect(page.getByRole("heading", { name: "Which currency should the demo use?" })).toBeVisible();
  await page.locator("#ob-display").click();
  await page.locator("#ob-display").fill("JPY");
  await page.getByRole("option", { name: /^JPY/ }).first().click();
  await page.getByRole("button", { name: "Use JPY" }).click();
  await expect(page).toHaveURL(/\/$/, { timeout: 30_000 });

  // Books in yen: no decimals, the yen sign, same story.
  await expect(page.getByRole("heading", { level: 1 })).toContainText("JPY 40,000");
  const key = page.locator("section[aria-label='Key figures']");
  await expect(key).toContainText("¥50,000");
  await expect(key).toContainText("¥40,000");
  await expect(key).not.toContainText("$");

  // The rescue in yen.
  await page.goto("/approvals");
  await approve(page, "Payment request to");
  await expect(page.getByText("Approved ¥40,000")).toBeVisible();
  await page.getByRole("button", { name: "Send request (simulated)" }).click();
  await page.getByRole("button", { name: "Customer accepts" }).click();
  await page.getByRole("button", { name: "Customer pays ¥40,000" }).click();
  await expect(page.getByText(/outstanding now JPY 20,000/)).toBeVisible(); // the queue moves to "Done" first
  await tab(page, "Needs approval");
  await approve(page, "Payroll payment to");
  await page.getByRole("button", { name: "Submit to sandbox bank" }).click();
  await expect(page.getByText(/JPY 80,000 settled to SBX-PAYROLL-0001/)).toBeVisible();
  await page.goto("/");
  await expect(page.locator("section[aria-label='Key figures']")).toContainText("¥10,000");

  // Demo scenarios: the chosen currency stays for later resets, and another one can be picked (KWD, 3 decimals).
  await page.goto("/demo");
  await expect(page.getByLabel("Currency for the next reset")).toHaveValue(/JPY/);
  await page.locator("#demo-books").click();
  await page.locator("#demo-books").fill("KWD");
  await page.getByRole("option", { name: /^KWD/ }).first().click();
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Reserve breach", exact: true }) });
  await expect(card).toContainText("KWD");
  await card.getByRole("button", { name: "Reset to this scenario" }).click();
  await expect(page.getByRole("dialog")).toContainText("with books in KWD");
  await page.getByLabel("Type RESET to confirm").fill("RESET");
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page.getByText("Reset to 'Reserve breach'")).toBeVisible();
  await page.goto("/");
  await expect(page.locator("section[aria-label='Key figures']")).toContainText("85,000.000");
});

test("shared demo: a display currency adds labelled approximate amounts; the books stay in USD", async ({ page }) => {
  await login(page);
  await resetTo(page, "Standard rescue");
  await page.evaluate(async () => {
    const me = await (await fetch("/api/auth/me")).json();
    await fetch("/api/me/preferences", { method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token }, body: JSON.stringify({ display_currency: "JPY" }) });
  });
  await page.goto("/");
  await expect(page.locator("section[aria-label='Key figures']")).toContainText("$50,000.00");
  const eq = page.getByTestId("equivalents");
  await expect(eq).toBeVisible();
  // Either real approximate yen amounts or an honest "no rate" note (the local stack may have no rate provider).
  await expect(eq).toContainText(/Approximately in JPY|No JPY rate is available/);
  await page.evaluate(async () => {
    const me = await (await fetch("/api/auth/me")).json();
    await fetch("/api/me/preferences", { method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token }, body: JSON.stringify({ clear: ["display_currency"] }) });
  });
  await page.goto("/");
  await expect(page.getByTestId("equivalents")).toHaveCount(0);
});
