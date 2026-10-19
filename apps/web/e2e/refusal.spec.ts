import { expect, test } from "@playwright/test";
import { item, login, password, resetTo, tab } from "./helpers";

test("customer refusal: explained, no money moves, plan becomes infeasible, payroll stays blocked", async ({ page }) => {
  test.skip(!password, "Set FLOWSENSE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  await login(page);
  await resetTo(page, "Customer refuses the request");

  await page.goto("/approvals");
  await tab(page, "In progress");
  const row = item(page, "Payment request to");
  await expect(row.getByText("Request sent")).toBeVisible();
  await row.getByRole("button", { name: "Customer refuses" }).click();
  await expect(page.getByText("Customer refused (no cash moved)")).toBeVisible();

  // The queue follows the item to "Done".
  const refused = item(page, "Payment request to");
  await expect(refused.getByText("Refused by customer")).toBeVisible();
  await expect(refused.getByText(/The customer declined\. No money moved/)).toBeVisible();
  await expect(refused.getByRole("button", { name: /Customer pays/ })).toHaveCount(0);

  await page.goto("/");
  await expect(page.locator("p", { hasText: /^\$50,000\.00$/ }).first()).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("You need an additional USD 40,000.00");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("No option allowed by your policy covers it");

  await page.goto("/rescue");
  await expect(page.getByRole("paragraph").filter({ hasText: /^No safe plan$/ })).toBeVisible();
  await expect(page.getByText(/refused a payment request; not re-proposed/)).toBeVisible();

  // The permitted-looking next step is still refused by the safety rules.
  await page.goto("/approvals");
  await tab(page, "Needs approval");
  await item(page, "Payroll payment to").getByRole("button", { name: "Review & approve" }).click();
  await expect(page.getByRole("dialog").getByText("Safety kernel pre-check: BLOCKED")).toBeVisible();
});
