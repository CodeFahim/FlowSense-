import { expect, test } from "@playwright/test";
import { approve, item, login, password, resetTo, tab } from "./helpers";

test("primary rescue journey through the real UI", async ({ page }) => {
  test.skip(!password, "Set FLOWSENSE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  await login(page);
  await resetTo(page, "Standard rescue");

  // Step 1: exact gap, in plain English.
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("You need an additional USD 40,000.00");
  await expect(page.getByText("Review 2 payments or requests needing approval")).toBeVisible();
  // Urgent places are in the main menu while they need the owner (not hidden under "More").
  const attention = page.getByRole("group", { name: "Needs attention" });
  await expect(attention.getByRole("link", { name: /Approvals/ })).toBeVisible();
  await expect(attention.getByRole("link", { name: "Rescue plan" })).toBeVisible();

  // Steps 2-3: approve and send the 40,000 request; acceptance is not cash.
  await page.goto("/approvals");
  await item(page, "Payment request to").getByRole("button", { name: "Review & approve" }).click();
  await expect(page.getByRole("dialog").getByText("Safety kernel pre-check: passes")).toBeVisible();
  await page.getByRole("dialog").getByRole("checkbox").check();
  await page.getByRole("dialog").getByRole("button", { name: "Approve $40,000.00" }).click();
  await expect(page.getByText("Approved $40,000.00")).toBeVisible();
  // The queue follows the item to "Ready to send".
  await page.getByRole("button", { name: "Send request (simulated)" }).click();
  await page.getByRole("button", { name: "Customer accepts" }).click();
  await expect(page.getByText("Customer accepted (no cash yet)")).toBeVisible();

  // Step 4: sandbox settlement of 40,000.
  await page.getByRole("button", { name: "Customer pays $40,000.00" }).click();
  await expect(page.getByText("outstanding now USD 20,000.00")).toBeVisible();

  // Step 5: approve the exact 80,000 payroll; the dialog shows the reserve impact.
  await tab(page, "Needs approval");
  await item(page, "Payroll payment to").getByRole("button", { name: "Review & approve" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("stays protected")).toBeVisible();
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "Approve $80,000.00" }).click();
  await expect(page.getByText("Approved $80,000.00")).toBeVisible();

  // Step 6: a double click still pays once; 10,000 left; resolved.
  await page.getByRole("button", { name: "Submit to sandbox bank" }).dblclick();
  await expect(page.getByText(/USD 80,000\.00 settled to SBX-PAYROLL-0001/)).toBeVisible();
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("The rescue is complete");
  // Nothing urgent left: the menu is back to its four main places.
  await expect(page.getByRole("group", { name: "Needs attention" })).toHaveCount(0);
  await expect(page.locator("p", { hasText: /^\$10,000\.00$/ }).first()).toBeVisible();

  await page.goto("/activity");
  await page.getByRole("button", { name: "Check record integrity" }).click();
  await expect(page.getByText(/records are intact/)).toBeVisible();
  await tab(page, "Receipts");
  await expect(page.getByText("R-00002")).toBeVisible();
  await expect(page.getByText("R-00003")).toHaveCount(0);
  void approve; // shared helper also used by other specs
});
