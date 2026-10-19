import { expect, test, type Page } from "@playwright/test";

// The "Start fresh" journey with a disposable account: sign up, confirm the profile (nothing preset),
// add cash, payroll and an invoice through the + Add menu, confirm the reserve, read the result,
// and use the assistant from several pages. Runs against the real backend.

const PW = "playwright fresh passphrase 1";

function todayPlus(days: number, tz = "Asia/Dhaka") {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date(Date.now() + days * 864e5));
}

async function pick(page: Page, id: string, text: string, option: RegExp) {
  await page.locator(`#${id}`).click();
  await page.locator(`#${id}`).fill(text);
  // Lists can reload (timezones are re-suggested after a country is chosen): wait for the option itself.
  await page.getByRole("option", { name: option }).first().click();
}

async function add(page: Page, label: string) {
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByRole("menuitem", { name: new RegExp(`^${label}`) }).click();
}

test("start fresh: sign up, set up with no presets, find the gap, ask the assistant", async ({ page }) => {
  const email = `pw-${Date.now()}@example.test`;
  await page.goto("/signup");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Full name").fill("Playwright Owner");
  await page.getByLabel("Username").fill(`pw_${Date.now()}`);
  await page.getByLabel("Confirm password").fill(PW);
  const pw = page.getByLabel("Password", { exact: true });
  await pw.fill(PW);
  // The eye toggle reveals the value without submitting or clearing it.
  await page.getByRole("button", { name: "Show password" }).first().click();
  await expect(pw).toHaveAttribute("type", "text");
  await expect(pw).toHaveValue(PW);
  await expect(page).toHaveURL(/\/signup$/);
  await page.getByRole("button", { name: "Hide password" }).first().click();
  await expect(pw).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/\/welcome$/);
  // The welcome page hides the header, so it has its own sign-out.
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await page.getByRole("button", { name: /Start my business/ }).click();
  await expect(page).toHaveURL(/\/setup\/business$/);
  // Nothing preset: no country, currency or timezone.
  await expect(page.locator("#bp-country")).toHaveValue("");
  await expect(page.locator("#bp-currency")).toHaveValue("");
  await expect(page.locator("#bp-tz")).toHaveValue("");
  await page.getByLabel("Business name").fill("Playwright Bakery");
  await pick(page, "bp-country", "Bangladesh", /^Bangladesh/);
  await expect(page.locator("#bp-currency")).toHaveValue(""); // a country only suggests
  await pick(page, "bp-currency", "USD", /^USD/);
  await pick(page, "bp-tz", "Dhaka", /Asia\/Dhaka/);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Save business" }).click();
  await expect(page).toHaveURL(/\/setup$/);

  await add(page, "Available cash");
  await page.getByLabel(/Amount available/).fill("5,000");
  await page.getByRole("button", { name: "Save cash" }).click();
  await expect(page.getByText("Available cash saved")).toBeVisible();

  await add(page, "Payroll or wages");
  await page.getByLabel("Paid to").fill("Bakery staff");
  await page.getByLabel(/^Amount/).fill("8,000");
  await page.getByLabel("Due date").fill(todayPlus(3));
  await page.getByLabel("Repeats").selectOption("none");
  await page.getByRole("button", { name: "Save payroll" }).click();
  await expect(page.getByText("Payroll saved")).toBeVisible();

  await page.getByRole("button", { name: "No customers owe me money" }).click();
  await page.getByRole("link", { name: /Review/ }).click();
  await page.getByLabel(/Cash reserve to always keep/).fill("1,000");
  await page.getByRole("button", { name: "Confirm and analyse my business" }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("USD 4,000.00");
  await expect(page.getByText("Projected shortage", { exact: true })).toBeVisible();

  // AI first: a quick action on Home answers from the user's own records without asking for them.
  await page.getByRole("button", { name: /^Analyse my business/ }).first().click();
  const sheet = page.getByRole("dialog", { name: "AI assistant" });
  await expect(sheet.getByText(/short USD 4,000\.00/).last()).toBeVisible();
  // A suggested action runs only after Confirm, is verified, and Home refreshes.
  await sheet.getByLabel("Message the assistant").fill("Record cash 9,500");
  await sheet.getByLabel("Message the assistant").press("Enter");
  await expect(sheet.getByText(/Record available cash of USD 9,500\.00/).last()).toBeVisible();
  await sheet.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(sheet.getByText(/Done and checked/).last()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText("$9,500.00").first()).toBeVisible();

  // The assistant is reachable from several pages and keeps the conversation.
  await page.getByRole("button", { name: "Open AI assistant" }).first().click();
  const chat = page.getByRole("dialog", { name: "AI assistant" });
  await chat.getByLabel("Message the assistant").fill("Where do I add a bill?");
  await chat.getByLabel("Message the assistant").press("Enter");
  await expect(chat.getByRole("link", { name: "Add a bill" })).toBeVisible();
  await page.keyboard.press("Escape");
  for (const path of ["/money-in", "/money-out", "/rescue", "/approvals", "/activity", "/settings", "/help"]) {
    await page.goto(path);
    await expect(page.getByRole("button", { name: "Open AI assistant" }).first()).toBeVisible();
  }
  await page.getByRole("button", { name: "Open AI assistant" }).first().click();
  await expect(page.getByRole("dialog", { name: "AI assistant" }).getByText("Where do I add a bill?")).toBeVisible();
});
