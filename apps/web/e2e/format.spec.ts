import { expect, test } from "@playwright/test";
import { configureFormatting, currencySymbol, money } from "../src/lib/format";

// The shared money formatter (no browser needed): narrow symbols, ISO 4217 decimals, locale placement,
// negatives and zero, codes where no symbol exists, and labels for shared symbols in mixed views.

const CURRENCIES = [
  { code: "USD", minor_units: 2 }, { code: "BDT", minor_units: 2 }, { code: "CNY", minor_units: 2 }, { code: "EUR", minor_units: 2 },
  { code: "GBP", minor_units: 2 }, { code: "JPY", minor_units: 0 }, { code: "KWD", minor_units: 3 }, { code: "CAD", minor_units: 2 },
];
const nb = (s: string) => s.replace(/ | /g, " ");

test("symbols, decimals and grouping for USD, BDT, CNY, EUR, GBP, JPY and KWD", () => {
  configureFormatting({ currencies: CURRENCIES, locale: "en-US" });
  expect(money(4_000_000, "USD")).toBe("$40,000.00");
  expect(money(6_210_000, "BDT")).toBe("৳62,100.00");
  expect(money(125_000, "CNY")).toBe("¥1,250.00");
  expect(money(125_000, "EUR")).toBe("€1,250.00");
  expect(money(125_000, "GBP")).toBe("£1,250.00");
  expect(money(125_000, "JPY")).toBe("¥125,000"); // no minor units
  expect(nb(money(1_250_500, "KWD"))).toBe("KWD 1,250.500"); // three decimals; no narrow symbol, so the code
  expect(currencySymbol("BDT")).toBe("৳");
});

test("negatives, zero, unknown codes, very large values and missing amounts", () => {
  configureFormatting({ currencies: CURRENCIES, locale: "en-US" });
  expect(money(-4_000_000, "USD")).toBe("-$40,000.00");
  expect(money(0, "BDT")).toBe("৳0.00");
  expect(nb(money(-1, "KWD"))).toBe("-KWD 0.001");
  expect(nb(money(12_345, "XYZ"))).toBe("XYZ 123.45"); // no symbol known: the ISO code
  expect(money(900_719_925_474_099, "USD")).toBe("$9,007,199,254,740.99"); // exact, no floating-point rounding
  expect(money(null, "USD")).toBe("—");
});

test("the user's locale decides placement and grouping", () => {
  configureFormatting({ currencies: CURRENCIES, locale: "de-DE" });
  expect(nb(money(125_000, "EUR"))).toBe("1.250,00 €");
  configureFormatting({ locale: "en-GB" });
  expect(money(125_000, "GBP")).toBe("£1,250.00");
  configureFormatting({ locale: "en-US" });
});

test("mixed-currency views label shared symbols", () => {
  configureFormatting({ currencies: CURRENCIES, locale: "en-US" });
  expect(money(4_000_000, "USD", { mixed: true })).toBe("$40,000.00 USD"); // "$" is shared with CAD
  expect(money(4_000_000, "CAD", { mixed: true })).toBe("$40,000.00 CAD");
  expect(money(100, "CNY", { mixed: true })).toBe("¥1.00 CNY"); // "¥" is shared with JPY
  expect(money(100, "BDT", { mixed: true })).toBe("৳1.00"); // unique symbol: no label needed
});
