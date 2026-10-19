import { expect, test } from "@playwright/test";
import { item, login, password, resetTo } from "./helpers";

test("kill switch refusal, then a fresh retry pays exactly once", async ({ page }) => {
  test.skip(!password, "Set FLOWSENSE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  await login(page);
  await resetTo(page, "Duplicate payroll submissions");

  await page.goto("/settings");
  await page.getByRole("button", { name: "Turn emergency stop on" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Block new payments" }).click();
  await expect(page.getByText(/Emergency stop is on\./)).toBeVisible();

  await page.goto("/approvals");
  await page.getByRole("button", { name: "Submit to sandbox bank" }).click();
  await expect(page.getByText("Refused by the safety rules — nothing was paid or sent")).toBeVisible();
  await expect(page.getByText("KILL_SWITCH_ACTIVE").first()).toBeVisible();

  await page.goto("/settings");
  await page.getByRole("button", { name: "Turn emergency stop off" }).click();
  await expect(page.getByText(/Emergency stop is on\./)).toHaveCount(0);

  // Same row, same tab: the earlier refusal must not be replayed.
  await page.goto("/approvals");
  await page.getByRole("button", { name: "Submit to sandbox bank" }).click();
  await expect(page.getByText("Paid — confirmed by the sandbox bank")).toBeVisible();
  await expect(item(page, "Payroll payment to").getByText("Settled", { exact: true })).toBeVisible();
});

test("bank timeout shows pending until a status check confirms it", async ({ page }) => {
  test.skip(!password, "Set FLOWSENSE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  await login(page);
  await resetTo(page, "Bank timeout and restart recovery");

  await page.goto("/approvals");
  await page.getByRole("button", { name: "Submit to sandbox bank" }).click();
  await expect(page.getByText("Submitted — waiting for bank confirmation")).toBeVisible();
  const row = item(page, "Payroll payment to");
  await expect(row.getByText("Submitted, pending bank")).toBeVisible();
  await expect(row.getByText(/set aside until the bank confirms/)).toBeVisible();

  await page.goto("/");
  // 90,000 settled, 80,000 set aside for the payment in flight: 10,000 available now.
  await expect(page.getByText("Money available now", { exact: true })).toBeVisible();
  await expect(page.locator("p", { hasText: /^\$10,000\.00$/ }).first()).toBeVisible();

  await page.goto("/approvals");
  await page.getByRole("button", { name: "Check bank status" }).click();
  await expect(page.getByText("Bank confirmed — reconciliation complete")).toBeVisible();
  await expect(item(page, "Payroll payment to").getByText("Settled", { exact: true })).toBeVisible();
});
