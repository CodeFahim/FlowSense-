// Display-only formatting. All money arrives as integer minor units from the
// backend; no financial rule is computed here. Decimal places come from ISO 4217
// (sent by the backend), so JPY shows 0 decimals and KWD shows 3.
//
// Money is shown with the currency's narrow symbol where one exists ("$1,250.00",
// "৳1,250.00", "¥1,250.00"), placed and grouped by the user's locale, and with the
// ISO code where it doesn't ("KWD 1,250.000"). In views that mix currencies, a symbol
// shared by several currencies ("$", "¥", "kr") gets the code next to it. Selectors,
// settings and explanatory text keep ISO codes. Display only: stored values never change.

const minorUnits = new Map<string, number>();
let locale: string | undefined = undefined; // undefined = the browser's own format
let deviceTime = false;
let businessTz = "UTC";

export function configureFormatting(opts: { currencies?: { code: string; minor_units: number }[]; locale?: string | null; deviceTime?: boolean; businessTimeZone?: string }) {
  opts.currencies?.forEach((c) => minorUnits.set(c.code, c.minor_units));
  if (opts.currencies) sharedSymbols = null;
  if (opts.locale !== undefined) {
    locale = opts.locale ?? undefined;
    sharedSymbols = null;
  }
  if (opts.deviceTime !== undefined) deviceTime = opts.deviceTime;
  if (opts.businessTimeZone) businessTz = opts.businessTimeZone;
}

export function digitsFor(currency: string): number {
  return minorUnits.get(currency) ?? 2;
}

let defaultCurrency = "USD";
export function setDefaultCurrency(code: string) {
  defaultCurrency = code;
}

// One shared formatter per (locale, currency, digits): Intl.NumberFormat is costly to build.
const formatters = new Map<string, Intl.NumberFormat | null>();
function currencyFormat(code: string, digits: number): Intl.NumberFormat | null {
  const key = `${locale ?? ""}|${code}|${digits}`;
  if (!formatters.has(key)) {
    let nf: Intl.NumberFormat | null = null;
    try {
      nf = new Intl.NumberFormat(locale, { style: "currency", currency: code, currencyDisplay: "narrowSymbol", minimumFractionDigits: digits, maximumFractionDigits: digits });
    } catch {
      nf = null; // unknown to this browser's Intl data: fall back to the ISO code
    }
    formatters.set(key, nf);
  }
  return formatters.get(key) ?? null;
}

/** The narrow symbol for a currency ("$", "৳"), or the ISO code when the locale has no symbol for it. */
export function currencySymbol(code: string): string {
  const nf = currencyFormat(code, 0);
  return nf?.formatToParts(0).find((p) => p.type === "currency")?.value ?? code;
}

// Symbols shared by more than one known currency ("$" for USD, CAD, AUD…), for mixed-currency views.
let sharedSymbols: Set<string> | null = null;
function isShared(symbol: string): boolean {
  if (!sharedSymbols) {
    const seen = new Map<string, number>();
    for (const code of minorUnits.keys()) {
      const sym = currencySymbol(code);
      if (sym !== code) seen.set(sym, (seen.get(sym) ?? 0) + 1);
    }
    sharedSymbols = new Set([...seen].filter(([, n]) => n > 1).map(([sym]) => sym));
  }
  return sharedSymbols.has(symbol);
}

function exactDecimal(minor: number, digits: number): string {
  // Integer minor units to an exact decimal string (no floating point); Intl formats the string exactly.
  const neg = minor < 0;
  const abs = BigInt(Math.abs(Math.trunc(minor)));
  const scale = BigInt(10) ** BigInt(digits);
  const major = abs / scale;
  const frac = digits ? (abs % scale).toString().padStart(digits, "0") : "";
  return `${neg ? "-" : ""}${major}${digits ? `.${frac}` : ""}`;
}

/**
 * "$1,250.00" from 125000 minor units in USD (the business currency when none is given).
 * `mixed`: the view shows several currencies, so a shared symbol gets its code ("$1,250.00 USD").
 */
export function money(minor: number | null | undefined, currency?: string, opts: { mixed?: boolean } = {}): string {
  if (minor === null || minor === undefined || Number.isNaN(minor)) return "—";
  const code = (currency ?? defaultCurrency).toUpperCase();
  const digits = digitsFor(code);
  const value = exactDecimal(minor, digits);
  const nf = currencyFormat(code, digits);
  if (!nf) {
    const plain = new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    const abs = value.replace(/^-/, "");
    return `${code} ${minor < 0 ? "-" : ""}${plain.format(abs as unknown as number)}`;
  }
  const text = nf.format(value as unknown as number);
  const sym = currencySymbol(code);
  return opts.mixed && sym !== code && isShared(sym) ? `${text} ${code}` : text;
}

/** Compact axis label, e.g. "40k". Display only. */
export function moneyShort(minor: number, currency?: string): string {
  const v = minor / 10 ** digitsFor(currency ?? defaultCurrency);
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

function tz(explicit?: string): string | undefined {
  if (deviceTime) return undefined; // the browser's own time zone
  return explicit ?? businessTz;
}

export function dateTime(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(locale ?? "en-GB", {
    timeZone: tz(timeZone),
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function dateOnly(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "—";
  const d = iso.length === 10 ? new Date(iso + "T12:00:00Z") : new Date(iso);
  return d.toLocaleDateString(locale ?? "en-GB", { timeZone: iso.length === 10 ? "UTC" : tz(timeZone), day: "2-digit", month: "short", year: "numeric" });
}

export function shortDate(iso: string): string {
  const d = iso.length === 10 ? new Date(iso + "T12:00:00Z") : new Date(iso);
  return d.toLocaleDateString(locale ?? "en-GB", { timeZone: "UTC", day: "2-digit", month: "short" });
}

export function timeZoneLabel(): string {
  return deviceTime ? Intl.DateTimeFormat().resolvedOptions().timeZone : businessTz;
}

export function label(s: string): string {
  const t = s.replace(/_/g, " ").toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}
