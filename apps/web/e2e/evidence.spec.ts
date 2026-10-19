import path from "node:path";
import { expect, test } from "@playwright/test";
import { login, password, resetTo } from "./helpers";

const samples = path.resolve(__dirname, "..", "..", "..", "samples", "evidence");

test("PDF evidence: upload, duplicate, scanned and fake files, persistence after reload", async ({ page }) => {
  test.skip(!password, "Set FLOWSENSE_DEMO_PASSWORD (scripts/e2e.ps1 does this)");
  await login(page);
  await resetTo(page, "Standard rescue");
  await page.goto("/money-in");
  await expect(page.getByText("INV-1042").first()).toBeVisible();

  // Two steps: read the PDF (nothing stored), review the text, then save.
  const read = async (file: string) => {
    await page.getByLabel("PDF file").setInputFiles(path.join(samples, file));
    await page.getByRole("button", { name: "Read PDF" }).click();
  };
  const readAndSave = async (file: string) => {
    await read(file);
    await expect(page.getByText("Review the extracted text before saving")).toBeVisible();
    await page.getByRole("button", { name: "Save as evidence" }).click();
  };

  await read("remittance-advice.pdf");
  await expect(page.getByText(/2 pages, .* characters\. Not stored yet\./)).toBeVisible();
  await expect(page.getByText("REMITTANCE ADVICE (synthetic demo document)", { exact: false }).first()).toBeVisible();
  await page.getByRole("button", { name: "Save as evidence" }).click();
  await expect(page.getByText(/^Saved as EV-1042-\d+$/)).toBeVisible();
  await expect(page.getByText(/Text from 2 pages attached as your upload \(not verified\)/)).toBeVisible();

  await readAndSave("remittance-advice.pdf");
  await expect(page.getByText(/already attached to INV-1042/)).toBeVisible();

  await read("scanned-image-only.pdf");
  await expect(page.getByText(/Not stored: This PDF has no text of its own/)).toBeVisible();
  await expect(page.getByText(/invoice evidence keeps only a PDF's own text/)).toBeVisible();

  await read("not-really-a.pdf");
  await expect(page.getByText(/Not stored: File is not a PDF/)).toBeVisible();

  await read("injection-note.pdf");
  await expect(page.getByText(/instruction-like wording\. It will be flagged and never followed/)).toBeVisible();
  await page.getByRole("button", { name: "Save as evidence" }).click();
  await expect(page.getByText(/Instruction-like text is flagged and never followed/)).toBeVisible();

  // Persisted: after a reload exactly two PDF evidence items are listed, all uploads marked as not verified.
  await page.reload();
  await expect(page.getByText(/Text extracted from a PDF/)).toHaveCount(2);
  await expect(page.getByText("Uploaded, not verified")).toHaveCount(3);
});
