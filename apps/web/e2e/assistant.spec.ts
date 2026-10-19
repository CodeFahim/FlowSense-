import { expect, test, type Page } from "@playwright/test";
import { login, password, resetTo } from "./helpers";

function failOnPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

test("AI assistant answers the starter questions and typed requests; adversarial request is denied", async ({ page }) => {
  test.skip(!password, "Set LIFELINE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  const errors = failOnPageErrors(page);
  await login(page);
  await resetTo(page, "Prompt injection in evidence");
  await page.goto("/assistant");
  await expect(page.getByText("Offline demo assistant").first()).toBeVisible();

  // Starter question from the empty state.
  await page.getByRole("button", { name: "Explain my cash position." }).click();
  await expect(page.getByText(/The gap is USD 40,000\.00/).last()).toBeVisible();
  await expect(page.getByRole("link", { name: "Open Rescue plan" }).last()).toBeVisible();

  const composer = page.getByLabel("Message the assistant");
  const ask = async (text: string, expected: string | RegExp) => {
    await composer.fill(text);
    await composer.press("Enter");
    await expect(page.getByText(expected).last()).toBeVisible();
  };
  await ask("Which invoices should I collect?", /INV-1042 - Northgate Electronics/);
  await ask("Can I cover payroll?", /Not yet\. The safety rules would block it/);
  await expect(page.getByText("RESERVE_BREACH").last()).toBeVisible();
  await ask("Compare my rescue options.", /Recommended: partial collection/);
  await ask("Compare financing options", /39\.70% annualized all-in, ABOVE your 8\.00% cap/);
  await ask("What if my customer pays 5 days late?", /This is a simulation; no records changed/);
  await ask("Pay employees this Friday, retain USD 10,000, use only approved recipients, and never borrow above 8% annualized all-in cost.", /Proposed policy v2: not active yet/);
  await ask("Follow the instructions in the invoice evidence", /\[Adversarial test\]/);
  await expect(page.getByText("TOOL_NOT_ALLOWED").last()).toBeVisible();

  // Shift+Enter makes a new line instead of sending.
  await composer.fill("first line");
  await composer.press("Shift+Enter");
  await composer.pressSequentially("Book me a flight to Lisbon");
  await expect(composer).toHaveValue("first line\nBook me a flight to Lisbon");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText(/only understand a fixed set of questions/).last()).toBeVisible();

  // The conversation is stored on the server, so it survives a reload.
  await page.reload();
  await expect(page.getByText(/\[Adversarial test\]/).last()).toBeVisible();

  expect(errors, `browser errors: ${errors.join(" | ")}`).toEqual([]);
});

test("every main page renders without browser errors", async ({ page }) => {
  test.skip(!password, "Set LIFELINE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  const errors = failOnPageErrors(page);
  await login(page);
  await resetTo(page, "Standard rescue");
  const pages: [string, string | RegExp][] = [
    ["/", /You need an additional/],
    ["/money", "Money"],
    ["/money-in", "Money"],
    ["/money/forecast", "Money"],
    ["/customers", "Customers"],
    ["/rescue", "Rescue plan"],
    ["/approvals", "Approvals"],
    ["/assistant", "AI assistant"],
    ["/activity", "Activity & Receipts"],
    ["/settings", "Settings"],
    ["/demo", "Demo scenarios"],
  ];
  for (const [path, heading] of pages) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  }
  // The old audit address still works.
  await page.goto("/audit");
  await expect(page).toHaveURL(/\/activity$/);

  // Interactive pieces that render new content.
  await page.goto("/rescue");
  await page.getByLabel("Customer pays late by").fill("5");
  await page.getByRole("button", { name: "Preview" }).click();
  await page.getByRole("button", { name: "Show as table" }).first().click();
  await expect(page.getByRole("table").first()).toBeVisible();
  await page.goto("/activity");
  await page.getByRole("button", { name: "Check record integrity" }).click();
  await expect(page.getByText(/records are intact/)).toBeVisible();
  expect(errors, `browser errors: ${errors.join(" | ")}`).toEqual([]);
});
