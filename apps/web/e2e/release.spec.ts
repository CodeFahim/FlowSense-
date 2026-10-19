import { expect, test, type Page } from "@playwright/test";

// The simplified experience end to end with a disposable account: the cash dialog (close and save),
// the four-item navigation, an already-paid expense in the cash flow history, a customer and invoice
// read from a document and reviewed before saving, and managing assistant chats.

const PW = "playwright release passphrase 3";

function todayPlus(days: number, tz = "Asia/Dhaka") {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date(Date.now() + days * 864e5));
}

/** A tiny text PDF with one line per page (pypdf reads the pages as separate lines). */
function makePdf(lines: string[]): Buffer {
  const objs: string[] = [];
  const kids = lines.map((_, i) => `${4 + 2 * i} 0 R`).join(" ");
  objs.push("<< /Type /Catalog /Pages 2 0 R >>");
  objs.push(`<< /Type /Pages /Kids [${kids}] /Count ${lines.length} >>`);
  objs.push("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
  lines.forEach((t, i) => {
    const esc = t.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
    const stream = `BT /F1 12 Tf 50 750 Td (${esc}) Tj ET`;
    objs.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents ${5 + 2 * i} 0 R >>`);
    objs.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  });
  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  objs.forEach((body, i) => {
    offsets.push(Buffer.byteLength(out));
    out += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = Buffer.byteLength(out);
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n` + offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

async function pick(page: Page, id: string, text: string, option: RegExp) {
  await page.locator(`#${id}`).click();
  await page.locator(`#${id}`).fill(text);
  await page.getByRole("option", { name: option }).first().click();
}

test("simplified flows: cash dialog, navigation, expenses, documents, chats", async ({ page }) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const stamp = Date.now();
  await page.goto("/signup");
  await page.getByLabel("Email address").fill(`pw-rel-${stamp}@example.test`);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Full name").fill("Release Tester");
  await page.getByLabel("Username").fill(`rel_${stamp}`);
  await page.getByLabel("Password", { exact: true }).fill(PW);
  await page.getByLabel("Confirm password").fill(PW);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
  await page.getByRole("button", { name: /Start my business/ }).click();
  await page.getByLabel("Business name").fill("Release Bakery");
  await pick(page, "bp-currency", "USD", /^USD/);
  await pick(page, "bp-tz", "Dhaka", /Asia\/Dhaka/);
  await page.getByRole("checkbox", { name: /I confirm/ }).check();
  await page.getByRole("button", { name: "Save business" }).click();
  await expect(page).toHaveURL(/\/setup$/);

  // 1. The cash dialog: Escape closes it and it stays closed; Save closes it and the figure shows.
  await page.goto("/setup/cash");
  const dialog = page.getByRole("dialog", { name: "Add available cash" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await page.waitForTimeout(800);
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: "Enter available cash" }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: "Enter available cash" }).click();
  await dialog.getByRole("button", { name: "Save cash" }).click();
  await expect(dialog.getByText(/Enter the amount of money in your account today/)).toBeVisible(); // refused, dialog stays open
  await dialog.getByLabel(/Amount available/).fill("5,000");
  await dialog.getByRole("button", { name: "Save cash" }).click();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/setup$/);
  await expect(page.getByText(/5,000/).first()).toBeVisible();

  // 2. Four main places; the rest under "More".
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" }).first();
  for (const name of ["Dashboard", "Money", "Customers", "AI Assistant"]) await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Approvals" })).toBeHidden();
  await nav.getByText("More", { exact: true }).click();
  await expect(nav.getByRole("link", { name: "Approvals" })).toBeVisible();
  await expect(page.getByText("Money received this month")).toBeVisible();

  // 3. An expense already paid shows in the cash flow history with the running balance.
  await page.goto("/money");
  await expect(page.getByRole("heading", { name: "Money", level: 1 })).toBeVisible();
  await page.getByRole("button", { name: "Add expense" }).click();
  const exp = page.getByRole("dialog", { name: "Add an expense or bill" });
  // The first choice is "Already paid" vs "Pay later"; the form and its cash effect follow it.
  await expect(exp.getByRole("radio", { name: "Pay later (unpaid bill)" })).toHaveAttribute("aria-checked", "true");
  await expect(exp.getByTestId("expense-effect")).toContainText("your cash doesn't change now");
  await exp.getByRole("radio", { name: "Already paid" }).click();
  await expect(exp.getByTestId("expense-effect")).toContainText("your recorded cash goes down by this amount once");
  await expect(exp.getByLabel("Repeats")).toHaveCount(0);
  await exp.getByLabel("Who you paid").fill("Flour Co");
  await exp.getByLabel("What it's for").fill("Flour");
  await exp.getByLabel(/^Amount/).fill("200");
  await exp.getByLabel("Date paid").fill(todayPlus(0));
  await exp.getByRole("button", { name: "Save paid expense" }).click();
  await expect(exp).toBeHidden();
  await expect(page.getByText(/Flour paid outside FlowSense on \d{2} \w{3} \d{4} \(recorded by owner\)/).first()).toBeVisible();
  await expect(page.getByText("$4,800.00").first()).toBeVisible();

  // 4. A customer and invoice read from a document, reviewed, then saved.
  await page.goto("/customers");
  await page.getByRole("button", { name: "Add from a document" }).first().click();
  const docDlg = page.getByRole("dialog", { name: "Add from a document" });
  const due = todayPlus(20);
  await docDlg.locator('input[type="file"]').first().setInputFiles({ name: "invoice.pdf", mimeType: "application/pdf", buffer: makePdf(["Invoice No: CB-501", "Bill To: Hotel Rosa", `Due Date: ${due}`, "Total: USD 1,250.00"]) });
  await docDlg.getByRole("button", { name: "Read this file" }).click();
  await expect(docDlg.getByLabel("Customer name")).toHaveValue("Hotel Rosa");
  await expect(docDlg.getByLabel("Invoice number")).toHaveValue("CB-501");
  await expect(docDlg.getByLabel(/Amount owed/)).toHaveValue("1250");
  await expect(docDlg.getByLabel("Due date")).toHaveValue(due);
  await expect(docDlg.getByText(/Read from document/).first()).toBeVisible();
  await docDlg.getByRole("button", { name: "Save customer and invoice" }).click();
  await expect(docDlg).toBeHidden();
  await expect(page.getByText("Hotel Rosa", { exact: true })).toBeVisible();
  await expect(page.getByText("$1,250.00").first()).toBeVisible();
  await expect(page.getByText("From a document", { exact: true })).toBeVisible();

  // 5. Chats from the assistant's profile icon: new, rename, delete.
  await page.getByRole("button", { name: "Open AI assistant" }).first().click();
  const sheet = page.getByRole("dialog", { name: "AI assistant" });
  await expect(sheet.getByText(/I checked your records/)).toBeVisible();
  await sheet.getByLabel("Message the assistant").fill("Where do I add a bill?");
  await sheet.getByLabel("Message the assistant").press("Enter");
  await expect(sheet.getByRole("link", { name: "Add a bill" })).toBeVisible();
  await sheet.getByRole("button", { name: "Your chats" }).click();
  await expect(sheet.getByRole("button", { name: /^Where do I add a bill\?/ })).toBeVisible();
  await sheet.getByRole("button", { name: "Rename Where do I add a bill?" }).click();
  await sheet.getByLabel("Chat name").fill("Bills");
  await sheet.getByRole("button", { name: "Save name" }).click();
  await expect(sheet.getByRole("button", { name: /^Bills/ })).toBeVisible();
  await sheet.getByRole("button", { name: "New chat" }).first().click();
  await expect(sheet.getByText("Hi! What would you like to know?")).toBeVisible();
  await sheet.getByRole("button", { name: "Your chats" }).click();
  await sheet.getByRole("button", { name: "Delete Bills" }).click();
  await sheet.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(sheet.getByRole("button", { name: "Delete Bills" })).toBeHidden();
  expect(errors, `browser errors: ${errors.join(" | ")}`).toEqual([]);
});
