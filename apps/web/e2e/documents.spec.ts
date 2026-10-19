import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { login, resetTo } from "./helpers";

// Real files through the real UI and API (synthetic documents in samples/documents, real PDF text extraction,
// real Poppler + Tesseract OCR in the API container), plus the assistant's scroll boundaries and the
// currency symbols beside amounts.

const DOCS = path.resolve(__dirname, "../../../samples/documents");
const file = (name: string, mimeType: string) => ({ name, mimeType, buffer: readFileSync(path.join(DOCS, name)) });
const PW = "playwright documents passphrase 7";

async function pick(page: Page, id: string, text: string, option: RegExp) {
  await page.locator(`#${id}`).click();
  await page.locator(`#${id}`).fill(text);
  await page.getByRole("option", { name: option }).first().click();
}

test("documents: licence fills the profile without overwriting, invoices map to the right fields, scans and photos are read, re-imports are blocked", async ({ page }) => {
  test.setTimeout(180_000);
  const stamp = Date.now();
  await page.goto("/signup");
  await page.getByLabel("Email address").fill(`pw-doc-${stamp}@example.test`);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Full name").fill("Document Tester");
  await page.getByLabel("Username").fill(`doc_${stamp}`);
  await page.getByLabel("Password", { exact: true }).fill(PW);
  await page.getByLabel("Confirm password").fill(PW);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
  await page.getByRole("button", { name: /Start my business/ }).click();

  // 1. Business licence -> business profile. A value the user already typed is kept; the document's is offered.
  await page.getByLabel("Business name").fill("My Typed Name");
  await page.getByRole("button", { name: "Upload licence" }).click();
  await page.locator('input[type="file"]').first().setInputFiles(file("licence-text.pdf", "application/pdf"));
  await page.getByRole("button", { name: "Read this file" }).click();
  await expect(page.getByText("Business licence or registration")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Business name")).toHaveValue("My Typed Name");
  await expect(page.getByText(/The document says “Brightwater Components \(synthetic\)”/)).toBeVisible();
  await expect(page.getByLabel("Place or area")).toHaveValue("Mirpur 10, Dhaka");
  await expect(page.getByText(/Found in licence-text\.pdf, page 1: “Nature of business: Textile trading”/)).toBeVisible();
  await page.getByRole("button", { name: "Use document value" }).click();
  await expect(page.getByLabel("Business name")).toHaveValue("Brightwater Components (synthetic)");
  await pick(page, "bp-currency", "BDT", /^BDT/);
  await pick(page, "bp-tz", "Dhaka", /Asia\/Dhaka/);
  await page.getByRole("checkbox", { name: /I confirm/ }).check();
  await page.getByRole("button", { name: "Save business" }).click();
  await expect(page).toHaveURL(/\/setup$/);

  // 2. A two-page text invoice -> customer and invoice fields, with destinations and page references.
  await page.goto("/customers");
  await page.getByRole("button", { name: /Add from (a document|an invoice photo or file)/ }).first().click();
  const dlg = page.getByRole("dialog", { name: "Add from a document" });
  await dlg.locator('input[type="file"]').first().setInputFiles(file("invoice-text.pdf", "application/pdf"));
  await dlg.getByRole("button", { name: "Read this file" }).click();
  await expect(dlg.getByText("Detected document:")).toBeVisible({ timeout: 30_000 });
  await expect(dlg.getByText("Detected document:").locator("..")).toContainText("Invoice");
  await expect(dlg.getByLabel("Customer name")).toHaveValue("Riverside Hotel (sample)");
  await expect(dlg.getByLabel("Email")).toHaveValue("accounts@riverside.example");
  await expect(dlg.getByLabel("Invoice number")).toHaveValue("BW-2026-117");
  await expect(dlg.getByLabel(/Amount owed/)).toHaveValue("62100");
  await expect(dlg.getByLabel("Invoice date")).toHaveValue("2026-10-02");
  await expect(dlg.getByLabel("Due date")).toHaveValue("2026-10-30");
  await expect(dlg.getByLabel("What it was for")).toHaveValue(/^Cotton towels × 120 @ 250\.00 = 30,000\.00; Bath robes/);
  await expect(dlg.getByText("→ saved to Invoice → amount owed")).toBeVisible();
  await expect(dlg.getByText("Found in invoice-text.pdf, page 2: “Amount due BDT 62,100.00”").first()).toBeVisible();
  const ref = dlg.getByTestId("doc-reference");
  await expect(ref).toContainText("Tax / VAT");
  await expect(ref).toContainText("8100");
  await expect(ref).toContainText("Subtotal");
  await dlg.getByRole("button", { name: "Save customer and invoice" }).click();
  await expect(dlg).toBeHidden();
  const row = page.locator("li").filter({ hasText: "Riverside Hotel (sample)" });
  await expect(row).toContainText("৳62,100.00");

  // Persisted after a refresh; an invoice is money owed, never cash.
  await page.reload();
  await expect(page.locator("li").filter({ hasText: "Riverside Hotel (sample)" })).toContainText("৳62,100.00");
  await page.goto("/");
  await expect(page.locator("section[aria-label='Key figures']")).toContainText("৳62,100.00");
  await expect(page.locator("section[aria-label='Key figures']")).toContainText("Not entered");

  // 3. The same file again: blocked as a duplicate import, invoice unticked.
  await page.goto("/customers");
  await page.getByRole("button", { name: /Add from (a document|an invoice photo or file)/ }).first().click();
  await dlg.locator('input[type="file"]').first().setInputFiles(file("invoice-text.pdf", "application/pdf"));
  await dlg.getByRole("button", { name: "Read this file" }).click();
  await expect(dlg.getByText(/was already imported as an invoice/)).toBeVisible({ timeout: 30_000 });
  await expect(dlg.getByText(/Invoice BW-2026-117 already exists/)).toBeVisible();
  await expect(dlg.getByRole("checkbox", { name: /Also add this invoice/ })).not.toBeChecked();

  // 4. A scanned PDF (page images only): rendered and read with OCR, values traced to page 2.
  await dlg.getByRole("button", { name: "Read a different file" }).click();
  await dlg.locator('input[type="file"]').first().setInputFiles(file("invoice-scanned.pdf", "application/pdf"));
  await dlg.getByRole("button", { name: "Read this file" }).click();
  await expect(dlg.getByLabel("Invoice number")).toHaveValue("BW-2026-117", { timeout: 60_000 });
  await expect(dlg.getByLabel(/Amount owed/)).toHaveValue("62100");
  await expect(dlg.getByText("Found in invoice-scanned.pdf, page 2: “Amount due BDT 62,100.00”").first()).toBeVisible();

  // 5. Two photos of the one invoice, read together.
  await dlg.getByRole("button", { name: "Read a different file" }).click();
  await dlg.locator('input[type="file"]').first().setInputFiles([file("invoice-photo-1.jpg", "image/jpeg"), file("invoice-photo-2.jpg", "image/jpeg")]);
  await dlg.getByRole("button", { name: "Read these 2 files" }).click();
  await expect(dlg.getByLabel("Customer name")).toHaveValue("Riverside Hotel (sample)", { timeout: 60_000 });
  await expect(dlg.getByLabel(/Amount owed/)).toHaveValue("62100");
  await expect(dlg.getByText(/Found in invoice-photo-2\.jpg/).first()).toBeVisible();
  await dlg.getByRole("button", { name: "Cancel" }).click();
  // Nothing extra was saved by the reads: still one invoice for the customer.
  await expect(page.locator("li").filter({ hasText: "Riverside Hotel (sample)" })).toContainText("1 open invoice");
});

test("an unreadable file shows a clear error and can be retried", async ({ page }) => {
  await login(page);
  await resetTo(page, "Standard rescue");
  await page.goto("/customers");
  await page.getByRole("button", { name: /Add from (a document|an invoice photo or file)/ }).first().click();
  const dlg = page.getByRole("dialog", { name: "Add from a document" });
  await dlg.locator('input[type="file"]').first().setInputFiles({ name: "broken.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n" + "\u0000garbage".repeat(40), "latin1") });
  await dlg.getByRole("button", { name: "Read this file" }).click();
  await expect(dlg.getByText(/damaged|could not be parsed/)).toBeVisible({ timeout: 30_000 });
  await expect(dlg.getByRole("button", { name: "Try again" })).toBeVisible();
});

async function chatBounds(page: Page) {
  const dlg = page.getByRole("dialog", { name: "AI assistant" });
  const head = await dlg.getByRole("heading", { name: "AI assistant" }).boundingBox();
  const input = await dlg.getByLabel("Message the assistant").boundingBox();
  const vh = page.viewportSize()!.height;
  return { headTop: head!.y, inputBottom: input!.y + input!.height, vh };
}

for (const size of [{ name: "desktop", width: 1280, height: 800 }, { name: "phone", width: 390, height: 740 }]) {
  test(`assistant (${size.name}): header and composer stay on screen, only messages scroll, new-message pill`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: size.width, height: size.height });
    await login(page);
    await resetTo(page, "Standard rescue");
    await page.goto("/");
    await page.getByRole("button", { name: /Open AI assistant/ }).filter({ visible: true }).first().click();
    const dlg = page.getByRole("dialog", { name: "AI assistant" });
    await expect(dlg).toBeVisible();
    const composer = dlg.getByLabel("Message the assistant");
    for (const q of ["Explain my cash shortage", "Which invoices should I collect?", "Compare my rescue options.", "Compare financing options", "Can I cover payroll?", "What if my customer pays 5 days late?"]) {
      await composer.fill(q);
      await composer.press("Enter");
      await expect(dlg.getByText("Checking your records")).toHaveCount(0, { timeout: 30_000 });
    }
    const list = dlg.getByTestId("chat-messages");
    const before = await chatBounds(page);
    expect(before.headTop).toBeGreaterThanOrEqual(0);
    expect(before.inputBottom).toBeLessThanOrEqual(before.vh + 1);
    // Wheel-scrolling the messages moves only the messages (no chaining to the dialog or page).
    await list.hover();
    await page.mouse.wheel(0, -3000);
    await page.mouse.wheel(0, 3000);
    await page.mouse.wheel(0, 3000);
    const after = await chatBounds(page);
    expect(after.headTop).toBeCloseTo(before.headTop, 0);
    expect(after.inputBottom).toBeCloseTo(before.inputBottom, 0);
    expect(await page.evaluate(() => document.scrollingElement!.scrollTop)).toBe(0);
    expect(await list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);

    // Scrolled up when an answer arrives: no jump, a "New messages" button instead.
    await page.route("**/api/assistant/chat", async (route) => {
      await new Promise((r) => setTimeout(r, 1500));
      await route.continue();
    });
    await composer.fill("Explain my cash shortage");
    await composer.press("Enter");
    await list.evaluate((el) => el.scrollTo({ top: 0 }));
    await list.dispatchEvent("scroll");
    await expect(dlg.getByRole("button", { name: "New messages" })).toBeVisible({ timeout: 15_000 });
    expect(await list.evaluate((el) => el.scrollTop)).toBeLessThan(50);
    await dlg.getByRole("button", { name: "New messages" }).click();
    await expect.poll(() => list.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(10);
    await page.unroute("**/api/assistant/chat");

    // Close: the page scrolls normally again. Reopen: the newest message is in view.
    await dlg.getByRole("button", { name: "Close assistant" }).click();
    await expect(dlg).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.style.overflow)).toBe("");
    await page.mouse.wheel(0, 600);
    await expect.poll(() => page.evaluate(() => document.scrollingElement!.scrollTop)).toBeGreaterThan(0);
    await page.getByRole("button", { name: /Open AI assistant/ }).filter({ visible: true }).first().click();
    await expect(dlg).toBeVisible();
    await expect.poll(() => list.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(10);

    // Resizing (e.g. a phone keyboard shrinking the viewport) keeps the composer visible.
    await page.setViewportSize({ width: size.width, height: Math.round(size.height * 0.6) });
    await expect.poll(async () => (await chatBounds(page)).inputBottom <= page.viewportSize()!.height + 1).toBe(true);
    expect((await chatBounds(page)).headTop).toBeGreaterThanOrEqual(0);
  });
}

test("amounts show currency symbols: USD sample", async ({ page }) => {
  await login(page);
  await resetTo(page, "Standard rescue");
  await page.goto("/");
  await expect(page.locator("section[aria-label='Key figures']")).toContainText("$50,000.00");
  await page.goto("/money");
  await expect(page.locator("section[aria-label=Summary]")).toContainText("$50,000.00");
  // Codes stay in labels and inputs.
  await page.goto("/money-in");
  await expect(page.getByText("$60,000.00").first()).toBeVisible();
});
