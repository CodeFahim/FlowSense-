import { expect, test } from "@playwright/test";
import { login, password, resetTo } from "./helpers";

// Two-step sign-up: format checks before continuing, inline errors that keep what was typed,
// server-side uniqueness, and the AI quick actions on a phone-sized screen.

const PW = "playwright signup passphrase 9";

test("sign-up validates each step inline and keeps the user's input", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const stamp = Date.now();
  const username = `pw_taken_${stamp}`;

  // An account that owns the username we will try to reuse.
  const first = await page.request.post("/api/auth/register", {
    data: { email: `pw-first-${stamp}@example.test`, full_name: "First Owner", username, password: PW, confirm_password: PW },
  });
  expect(first.ok()).toBeTruthy();
  await page.context().clearCookies();

  await page.goto("/signup");
  await expect(page.getByText("Step 1 of 2")).toBeVisible();
  const email = page.getByLabel("Email address");
  await email.fill("amina@gmail");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText(/isn't a valid domain/)).toBeVisible();
  await expect(email).toBeFocused();
  // A likely typo in a popular domain is offered as a one-click fix.
  await email.fill(`amina-${stamp}@gmial.com`);
  await page.getByRole("button", { name: `amina-${stamp}@gmail.com` }).click();
  await expect(email).toHaveValue(`amina-${stamp}@gmail.com`);
  await page.keyboard.press("Enter");

  await expect(page.getByText("Step 2 of 2")).toBeVisible();
  await expect(page.getByLabel("Full name")).toBeFocused();
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Enter your full name.")).toBeVisible();
  await expect(page.getByText("Choose a username.")).toBeVisible();

  await page.getByLabel("Full name").fill("Amina Rahman");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Type of business").fill("Clothing or tailoring");
  await page.getByLabel("Business or shop name").fill("Rahman Tailors");
  await page.getByLabel("Password", { exact: true }).fill(PW);
  await page.getByLabel("Confirm password").fill(PW + "x");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("The passwords don't match.")).toBeVisible();
  await expect(page.getByLabel("Confirm password")).toBeFocused();
  // Both password fields have their own show/hide control.
  await expect(page.getByRole("button", { name: "Show password" })).toHaveCount(2);

  await page.getByLabel("Confirm password").fill(PW);
  await page.getByRole("button", { name: "Create account" }).click();
  // The server says the username is taken; everything else is kept.
  await expect(page.getByText("This username is already taken. Try another.")).toBeVisible();
  await expect(page.getByLabel("Business or shop name")).toHaveValue("Rahman Tailors");
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue(PW);

  // Going back to change the email keeps step 2.
  await page.getByRole("button", { name: "Change" }).click();
  await expect(page.getByLabel("Email address")).toHaveValue(`amina-${stamp}@gmail.com`);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByLabel("Full name")).toHaveValue("Amina Rahman");

  await page.getByLabel("Username").fill(`amina_${stamp}`);
  await page.getByLabel("Username").blur();
  await expect(page.getByText("Available", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/welcome$/);

  // The new username signs in.
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.goto("/login");
  await page.getByLabel("Email or username").fill(`amina_${stamp}`);
  await page.getByLabel("Password", { exact: true }).fill(PW);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
  expect(errors, `browser errors: ${errors.join(" | ")}`).toEqual([]);
});

test("phone: the AI quick actions work from Home and answer from the user's records", async ({ page }) => {
  test.skip(!password, "Set FLOWSENSE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  await page.setViewportSize({ width: 360, height: 780 });
  await login(page);
  await resetTo(page, "Standard rescue");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Ask your AI assistant" })).toBeVisible();
  await page.getByRole("button", { name: /^Help me plan payments/ }).first().click();
  const sheet = page.getByRole("dialog", { name: "AI assistant" });
  await expect(sheet.getByText(/Payments due in the next 30 days/).last()).toBeVisible();
  await expect(sheet.getByText(/not covered yet/).last()).toBeVisible();
  // Quick actions stay one tap away inside the conversation.
  await sheet.getByRole("group", { name: "Quick questions" }).getByRole("button", { name: "Explain my cash shortage" }).click();
  await expect(sheet.getByText(/The gap is USD 40,000\.00/).last()).toBeVisible();
  const noSideScroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  expect(noSideScroll).toBeTruthy();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Open AI assistant" }).first()).toBeVisible();
});
