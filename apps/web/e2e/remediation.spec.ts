import { expect, test } from "@playwright/test";
import { approve, login, resetTo, tab } from "./helpers";

// Fixes from the 18 Oct competition test report, checked in a real browser against the running stack.

test("a slow session check shows a status with retry and sign-in, and a temporary failure keeps you signed in", async ({ page }) => {
  await login(page);
  // Simulate a sleeping API: /api/auth/me never answers until released.
  let release = false;
  await page.route("**/api/auth/me", async (route) => {
    if (release) return route.continue();
    await new Promise((r) => setTimeout(r, 14_000)); // longer than the 12 s bound
    return route.abort("timedout").catch(() => undefined);
  });
  await page.goto("/money");
  const status = page.getByTestId("session-check");
  await expect(status).toBeVisible({ timeout: 8_000 });
  await expect(status).toContainText("FlowSense is waking up");
  await expect(status).toContainText("You stay signed in");
  await expect(status.getByRole("link", { name: "Go to sign-in" })).toBeVisible();
  release = true;
  await status.getByRole("button", { name: "Try again now" }).click();
  // Same session, no sign-in needed.
  await expect(page.getByRole("tab", { name: "History" }).or(page.getByText("Cash flow history")).first()).toBeVisible({ timeout: 20_000 });
  await expect(page).toHaveURL(/\/money$/);
});

test("an expired or missing session goes to sign-in with an explanation", async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto("/");
  await expect(page).toHaveURL(/\/login\?signed_out=1$/, { timeout: 15_000 });
  await expect(page.getByText("Sign in to continue. If you were signed in before, that session has ended.")).toBeVisible();
  await ctx.close();
});

test("opening balance is not money received; labels say where the balance comes from", async ({ page }) => {
  await login(page);
  await resetTo(page, "Standard rescue");
  await page.goto("/money");
  const summary = page.getByRole("region", { name: "Summary" }).or(page.locator("section[aria-label=Summary]"));
  await expect(summary.getByText("Settled cash balance")).toBeVisible();
  await expect(summary.getByText(/synthetic sample opening balance.*No real bank is connected/)).toBeVisible();
  await expect(summary.getByText(/^Received in /)).toBeVisible();
  await expect(summary.locator("p", { hasText: /^\$0\.00$/ }).first()).toBeVisible();
  await expect(page.getByText("what the bank confirms")).toHaveCount(0);
  await page.goto("/");
  await expect(page.getByText("Money received this month").locator("..").locator("..")).toContainText("$0.00");
});

test("rescue progress keeps the funding request and payroll approvals apart", async ({ page }) => {
  await login(page);
  await resetTo(page, "Standard rescue");
  await page.goto("/approvals");
  await approve(page, "Payment request to");
  await expect(page.getByText("Approved $40,000.00")).toBeVisible();
  await page.goto("/rescue");
  const progress = page.getByRole("list", { name: "Rescue progress" });
  const fund = progress.locator("li").filter({ hasText: "Approve the funding request" });
  await expect(fund).toContainText("Now");
  await expect(fund).toContainText("Approved, ready to send");
  const pay = progress.locator("li").filter({ hasText: /Approve and pay/ });
  await expect(pay).toContainText("Next");
  await expect(pay).toContainText("Needs approval after the money settles");
  // Never "done" next to "waiting for approval".
  await expect(progress.locator("li").filter({ hasText: "Done" }).filter({ hasText: /Waiting for owner approval|Needs approval/ })).toHaveCount(0);
  // Sent: the funding approval is done, the money is awaited, payroll still waits for settlement.
  await page.goto("/approvals");
  await tab(page, "Ready to send");
  await page.getByRole("button", { name: "Send request (simulated)" }).click();
  await page.getByRole("button", { name: "Customer accepts" }).click();
  await expect(page.getByText("Customer accepted (no cash yet)")).toBeVisible();
  await page.goto("/rescue");
  await expect(fund).toContainText("Done");
  const wait = progress.locator("li").filter({ hasText: "Wait for the money" });
  await expect(wait).toContainText("Now");
  await expect(wait).toContainText("Accepted, awaiting settlement");
  await expect(pay).toContainText("Needs approval after the money settles");
});

test("a what-if expense is shown as money out", async ({ page }) => {
  await login(page);
  await resetTo(page, "Standard rescue");
  await page.goto("/rescue");
  await page.getByLabel(/Surprise expense/).fill("1000");
  await page.getByRole("button", { name: "Preview" }).click();
  await page.getByText("Assumptions and dated money movements").click();
  await expect(page.getByText(/money out, Emergency expense \(what-if\) −\$1,000\.00/)).toBeVisible();
  await expect(page.getByText(/money in, Emergency expense/)).toHaveCount(0);
});

test("overdue customer invoices are not labelled next due", async ({ page }) => {
  await login(page);
  await resetTo(page, "Standard rescue");
  await page.goto("/customers");
  await expect(page.getByText(/Was due .* \(overdue\)/).first()).toBeVisible();
  await expect(page.getByText(/Next due 9 Oct/)).toHaveCount(0);
});

test("assistant drawer has one scrolling area and wrapped quick questions at 360 px", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await login(page);
  await page.getByRole("button", { name: /assistant/i }).first().click();
  const dialog = page.getByRole("dialog", { name: "AI assistant" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Explain my cash shortage" }).first().click();
  await expect(dialog.getByText(/Checking your records/)).toHaveCount(0, { timeout: 45_000 });
  const scrollers = await dialog.evaluate((d) =>
    [d, ...Array.from(d.querySelectorAll("*"))].filter((el) => {
      const s = getComputedStyle(el);
      return (s.overflowY === "auto" || s.overflowY === "scroll") && el.scrollHeight > el.clientHeight + 1;
    }).map((el) => `${el.tagName}.${el.className}`.slice(0, 120)),
  );
  expect(scrollers.length, JSON.stringify(scrollers)).toBeLessThanOrEqual(1);
  const chips = dialog.getByRole("group", { name: "Quick questions" });
  if (await chips.count()) {
    const overflowX = await chips.first().evaluate((el) => el.scrollWidth > el.clientWidth + 1);
    expect(overflowX).toBe(false);
  }
  const pageOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(pageOverflow).toBe(false);
});
