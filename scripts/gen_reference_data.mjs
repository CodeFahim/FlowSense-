// Regenerates the currency and region reference data used by the API.
//
//   node scripts/gen_reference_data.mjs
//
// Sources (both maintained upstream; re-run this script to refresh):
//   - ISO 4217 "List One" (current currencies), published by SIX on behalf of ISO:
//     https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
//     Gives the active currency codes, numeric codes and minor units (decimal places).
//   - Unicode CLDR supplemental currencyData (cldr-json, cldr-core):
//     https://raw.githubusercontent.com/unicode-org/cldr-json/main/cldr-json/cldr-core/supplemental/currencyData.json
//     Gives which currency each country/region currently uses. Used only to *suggest* a currency.
// English display names come from the ICU data bundled with Node (Intl.DisplayNames).
//
// Output: services/api/app/reference/currencies.json and regions.json (committed).

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ISO_URL = "https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml";
const CLDR_URL = "https://raw.githubusercontent.com/unicode-org/cldr-json/main/cldr-json/cldr-core/supplemental/currencyData.json";
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "services", "api", "app", "reference");

// Precious metals, testing and supranational units appear in List One with numeric
// minor units in a few cases; they are not fiat money for a business.
const NOT_FIAT = new Set(["XAU", "XAG", "XPD", "XPT", "XDR", "XSU", "XUA", "XBA", "XBB", "XBC", "XBD", "XTS", "XXX"]);

const titleCase = (s) =>
  s
    .toLowerCase()
    .replace(/(^|[\s(\-'’,.])([a-zà-ÿ])/g, (_, p, c) => p + c.toUpperCase())
    .replace(/\b(Of|The|And|Da|De)\b/g, (w) => w.toLowerCase());

async function get(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return res.text();
}

const xml = await get(ISO_URL);
const published = /Pblshd="([^"]+)"/.exec(xml)?.[1];
const curName = new Intl.DisplayNames(["en"], { type: "currency" });
const regionName = new Intl.DisplayNames(["en"], { type: "region" });

const currencies = new Map();
for (const m of xml.matchAll(/<CcyNtry>([\s\S]*?)<\/CcyNtry>/g)) {
  const e = m[1];
  const code = /<Ccy>([A-Z]{3})<\/Ccy>/.exec(e)?.[1];
  const minor = /<CcyMnrUnts>(\d+)<\/CcyMnrUnts>/.exec(e)?.[1];
  const isoName = /<CcyNm[^>]*>([^<]+)<\/CcyNm>/.exec(e)?.[1];
  const isFund = /<CcyNm[^>]*IsFund="true"/.test(e);
  const entity = /<CtryNm>([^<]+)<\/CtryNm>/.exec(e)?.[1] ?? "";
  if (!code || minor === undefined || isFund || NOT_FIAT.has(code) || entity.startsWith("ZZ")) continue;
  const numeric = /<CcyNbr>(\d+)<\/CcyNbr>/.exec(e)?.[1];
  const c = currencies.get(code) ?? {
    code,
    numeric,
    name: curName.of(code) !== code ? curName.of(code) : isoName,
    iso_name: isoName,
    minor_units: Number(minor),
    used_in: [],
  };
  c.used_in.push(titleCase(entity.replace(/&amp;/g, "&")));
  currencies.set(code, c);
}

const cldr = JSON.parse(await get(CLDR_URL));
const regions = [];
for (const [region, list] of Object.entries(cldr.supplemental.currencyData.region)) {
  if (!/^[A-Z]{2}$/.test(region) || ["EU", "ZZ", "UN", "EZ", "QO"].includes(region)) continue;
  const name = regionName.of(region);
  if (!name || name === region) continue;
  const current = list
    .flatMap((o) => Object.entries(o))
    .filter(([code, info]) => !info._to && info._tender !== "false" && currencies.has(code))
    .map(([code]) => code);
  if (current.length) regions.push({ code: region, name, currencies: current });
}
regions.sort((a, b) => a.name.localeCompare(b.name, "en"));

const sorted = [...currencies.values()].sort((a, b) => a.code.localeCompare(b.code));
const meta = { iso4217_published: published, cldr_version: cldr.supplemental.version._cldrVersion, generated: new Date().toISOString().slice(0, 10) };
writeFileSync(join(OUT, "currencies.json"), JSON.stringify({ source: ISO_URL, ...meta, currencies: sorted }, null, 1) + "\n");
writeFileSync(join(OUT, "regions.json"), JSON.stringify({ source: CLDR_URL, ...meta, regions }, null, 1) + "\n");
console.log(`${sorted.length} active fiat currencies (ISO 4217 list published ${published}); ${regions.length} regions (CLDR ${meta.cldr_version}).`);
