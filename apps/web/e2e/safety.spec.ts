import { expect, test } from "@playwright/test";
import { approve, item, login, password, resetTo, tab } from "./helpers";

test.beforeEach(async ({ page }) => {
  test.skip(!password, "Set LIFELINE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  await login(page);
});

test("partial settlement: blocked-payroll reasons follow live balances, replan pays exactly once", async ({ page }) => {
  await resetTo(page, "Partial settlement");
  await page.goto("/approvals");
  await tab(page, "Needs approval");
  await expect(item(page, "Payroll payment to").getByText(/USD 50,000\.00 of settled/)).toBeVisible();

  await tab(page, "In progress");
  await page.getByRole("button", { name: "Customer pays part…" }).click();
  await page.getByLabel("Custom payment amount in USD").fill("20000");
  await page.getByRole("button", { name: "Simulate payment" }).click();
  await expect(page.getByText("USD 20,000.00 received for INV-1042")).toBeVisible();
  // Regression: the warning uses the new settled cash (70,000), not the value at proposal time.
  await tab(page, "Needs approval");
  await expect(item(page, "Payroll payment to").getByText(/USD 70,000\.00 of settled/)).toBeVisible();
  await expect(page.getByText(/USD 50,000\.00 of settled/)).toHaveCount(0);

  await approve(page, "(USD 20,000.00) to close");
  await page.getByRole("button", { name: "Send request (simulated)" }).click();
  await page.getByRole("button", { name: "Customer pays $20,000.00" }).click();
  await expect(page.getByText("outstanding now USD 20,000.00")).toBeVisible();
  await tab(page, "Needs approval");
  await expect(item(page, "Payroll payment to").getByText("Would be blocked right now")).toHaveCount(0);

  await approve(page, "Payroll payment to");
  await page.getByRole("button", { name: "Submit to sandbox bank" }).click();
  await expect(page.getByText("Paid — confirmed by the sandbox bank")).toBeVisible();
  await page.goto("/");
  await expect(page.locator("p", { hasText: /^\$10,000\.00$/ }).first()).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("The rescue is complete");
});

test("beneficiary change revokes the approval; verify, rebind and re-approve pays the new account", async ({ page }) => {
  await resetTo(page, "Beneficiary changed after approval");
  await page.goto("/approvals");
  const recipient = page.locator("li").filter({ hasText: "SBX-PAYROLL-0001" }).filter({ hasText: "version 1" }).last();
  await recipient.getByRole("button", { name: "Request account change" }).click();
  await page.getByLabel("New account reference").fill("SBX-PAYROLL-0002");
  await page.getByRole("button", { name: "Submit change request" }).click();
  await page.getByRole("button", { name: "Apply change" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Apply and revoke approvals" }).click();
  await expect(page.getByText("Change applied; affected approvals revoked")).toBeVisible();
  await tab(page, "Ready to send");
  await expect(item(page, "Payroll payment to").getByText(/bound to v1; current details v2/)).toBeVisible();

  await page.getByRole("button", { name: "Submit to sandbox bank" }).click();
  await expect(page.getByText("Refused by the safety rules — nothing was paid or sent")).toBeVisible();
  for (const code of ["RECIPIENT_VERSION_CHANGED", "RECIPIENT_UNVERIFIED", "APPROVAL_REVOKED"]) {
    await expect(page.getByText(code).first()).toBeVisible();
  }

  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(page.getByText("Verified against the sandbox registry")).toBeVisible();
  await page.getByRole("button", { name: "Rebind to recipient v2" }).click();
  await approve(page, "Payroll payment to");
  await page.getByRole("button", { name: "Submit to sandbox bank" }).click();
  await expect(page.getByText("USD 80,000.00 settled to SBX-PAYROLL-0002")).toBeVisible();
});

test("reserve breach is refused at approval and the plan is infeasible", async ({ page }) => {
  await resetTo(page, "Reserve breach");
  await page.goto("/approvals");
  await item(page, "Payroll payment to").getByRole("button", { name: "Review & approve" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Safety kernel pre-check: BLOCKED")).toBeVisible();
  await expect(dialog.getByText(/would leave USD 5,000\.00, below the protected reserve/).first()).toBeVisible();
  // Blocked: no way to "approve anyway"; the recovery path is shown instead.
  await expect(dialog.getByText("Can't be approved right now")).toBeVisible();
  await expect(dialog.getByRole("button", { name: /^Approve/ })).toHaveCount(0);
  await expect(dialog.getByRole("link", { name: "Resolve cash shortage" })).toBeVisible();
  // The backend refuses it too (isolated sandbox scenario), with the exact reason.
  const actions = await (await page.request.get("/api/actions")).json();
  const payroll = actions.find((a: { type: string; status: string }) => a.type === "payroll_payment" && a.status === "proposed");
  const csrf = (await (await page.request.get("/api/auth/me")).json()).csrf_token;
  const refused = await page.request.post(`/api/actions/${payroll.id}/approve`, { headers: { "X-CSRF-Token": csrf }, data: {} });
  expect(refused.status()).toBe(409);
  expect(JSON.stringify(await refused.json())).toContain("RESERVE_BREACH");
  await dialog.getByRole("link", { name: "Resolve cash shortage" }).click();
  await expect(page).toHaveURL(/\/rescue$/);
  await expect(page.getByRole("paragraph").filter({ hasText: /^No safe plan$/ })).toBeVisible();
  await expect(page.getByText("Still missing:")).toContainText("$5,000.00");
});

test("three concurrent payroll submissions pay exactly once", async ({ page }) => {
  await resetTo(page, "Duplicate payroll submissions");
  await page.getByRole("button", { name: "Fire 3 concurrent submissions" }).click();
  await expect(page.getByText(/HTTP 200/)).toHaveCount(1);
  await expect(page.getByText(/HTTP 409/)).toHaveCount(2);
  await page.goto("/");
  await expect(page.locator("p", { hasText: /^\$10,000\.00$/ }).first()).toBeVisible();
});
