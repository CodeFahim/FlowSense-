"use client";

import { Building2, FileText, Info } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useCurrencyItems } from "@/components/converter";
import { DocReader, DocTypeBadge, FieldSource, SuggestionNotes, type Suggestion } from "@/components/doc-reader";
import { useSession } from "@/components/shell";
import { Button, Combobox, Field, Input, Notice, PageHeader } from "@/components/ui";
import { apiGet, apiPost, apiPut } from "@/lib/api";
import { describeError } from "@/lib/outcomes";

type Zones = { suggested: string[]; timezones: { name: string; offset_now: string }[] };
type Licence = { registration_number?: string; owner_name?: string; issue_date?: string; expiry_date?: string };

/**
 * Business profile. Nothing is preselected: country, currency and timezone are independent choices.
 * A country only *suggests* currencies and timezones (shown first, marked "Suggested"); nothing is applied for you.
 */
export default function BusinessProfilePage() {
  const router = useRouter();
  const { me, ref, reload } = useSession();
  const b = me.business;
  const [name, setName] = useState(b.name);
  const [place, setPlace] = useState(b.place ?? "");
  const [type, setType] = useState(b.business_type ?? "");
  const [country, setCountry] = useState<string | null>(b.country);
  const [currency, setCurrency] = useState<string | null>(b.currency);
  const [tz, setTz] = useState<string | null>(b.timezone);
  const [confirm, setConfirm] = useState(false);
  const [lic, setLic] = useState<Licence>({});
  const [read, setRead] = useState<Suggestion | null>(null);
  const [showReader, setShowReader] = useState(false);
  const [zones, setZones] = useState<Zones | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const items = useCurrencyItems();
  const fixture = b.kind !== "workspace";

  useEffect(() => {
    apiGet<Zones>(`/api/reference/timezones${country ? `?country=${country}` : ""}`).then(setZones, () => undefined);
  }, [country]);
  useEffect(() => {
    apiGet<{ licence: Licence | null }>("/api/business/profile").then((p) => setLic(p.licence ?? {}), () => undefined);
  }, []);

  // Values read from a licence fill the form for review. Nothing is saved until "Save business".
  // Values read from a document fill only empty fields. A field that already has a different value keeps it,
  // and the document's value is offered next to it ("Use document value"): nothing is overwritten silently.
  const [conflicts, setConflicts] = useState<Record<string, string>>({});
  const current: Record<string, string> = {
    name, place, business_type: type, country: country ?? "",
    registration_number: lic.registration_number ?? "", owner_name: lic.owner_name ?? "", issue_date: lic.issue_date ?? "", expiry_date: lic.expiry_date ?? "",
  };
  const setters: Record<string, (v: string) => void> = {
    name: setName, place: setPlace, business_type: setType, country: (v) => setCountry(v),
    registration_number: (v) => setLic((l) => ({ ...l, registration_number: v })), owner_name: (v) => setLic((l) => ({ ...l, owner_name: v })),
    issue_date: (v) => setLic((l) => ({ ...l, issue_date: v })), expiry_date: (v) => setLic((l) => ({ ...l, expiry_date: v })),
  };
  function applySuggestion(sg: Suggestion) {
    const clash: Record<string, string> = {};
    for (const [k, f] of Object.entries(sg.fields)) {
      if (!setters[k]) continue;
      const now = (current[k] ?? "").trim();
      if (!now) setters[k](f.value);
      else if (now.toLowerCase() !== f.value.trim().toLowerCase()) clash[k] = f.value;
    }
    setConflicts(clash);
    setRead(sg);
    setShowReader(false);
  }
  function applyDocValue(k: string) {
    setters[k](conflicts[k]);
    setConflicts((c) => Object.fromEntries(Object.entries(c).filter(([key]) => key !== k)));
  }
  const offer = (k: string) =>
    conflicts[k] ? (
      <span className="mt-1 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">The document says “{conflicts[k]}”. Your saved value is kept.</span>
        <button type="button" onClick={() => applyDocValue(k)} className="font-medium text-accent-ink underline">Use document value</button>
      </span>
    ) : null;
  const src = (k: string) => (read ? read.fields[k] : undefined);

  const regionItems = useMemo(() => ref.regions.map((r) => ({ value: r.code, label: r.name, keywords: r.code })), [ref.regions]);
  const zoneItems = useMemo(() => (zones?.timezones ?? []).map((z) => ({ value: z.name, label: z.name.replace(/_/g, " "), hint: z.offset_now })), [zones]);
  const suggestedCurrencies = useMemo(() => (country ? ref.regions.find((r) => r.code === country)?.currencies ?? [] : []), [country, ref.regions]);
  const deviceTz = typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : null;
  const locked = me.accounting_currency.locked && !fixture;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!currency || !tz) {
      setErr("Choose the accounting currency and the business timezone.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await apiPut("/api/business/profile", {
        name,
        place: place || null,
        business_type: type || null,
        country,
        currency,
        timezone: tz,
        confirm_currency_and_timezone: confirm,
        licence: Object.values(lic).some((v) => v) ? { registration_number: lic.registration_number || null, owner_name: lic.owner_name || null, issue_date: lic.issue_date || null, expiry_date: lic.expiry_date || null } : null,
      });
      if (read) await apiPost("/api/documents/link", { document_ids: read.documents.map((d) => d.id), record_type: "business", record_id: b.id }).catch(() => undefined);
      await reload();
      router.push(b.kind === "workspace" ? "/setup" : "/settings");
    } catch (e2) {
      setErr(describeError(e2).text ?? describeError(e2).title);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader title="Your business" description="Step 1 of setup. Only the name, accounting currency and timezone are required." />
      {me.role !== "owner" && <Notice tone="info">Only the owner can change the business profile.</Notice>}
      {me.role === "owner" && !fixture && (
        <div className="rounded-2xl border border-line bg-surface p-5 sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="flex items-center gap-2 font-medium"><FileText className="h-4 w-4 text-accent" aria-hidden /> Fill this in from your business licence <span className="text-sm font-normal text-muted">(optional)</span></p>
              <p className="mt-0.5 text-sm text-muted">Upload a photo or PDF of your trade licence or registration. FlowSense fills the form below for you to check. Or just type it.</p>
            </div>
            {!showReader && <Button variant="secondary" onClick={() => setShowReader(true)}>{read ? "Read another document" : "Upload licence"}</Button>}
          </div>
          {showReader && <div className="mt-4"><DocReader purpose="business" onRead={applySuggestion} hint="A trade licence, registration certificate or similar. PDF, photo, Word or Excel." /></div>}
          {read && <div className="mt-4 space-y-2"><DocTypeBadge s={read} /><SuggestionNotes s={read} /></div>}
        </div>
      )}
      <form onSubmit={submit} className="space-y-5 rounded-2xl border border-line bg-surface p-5 sm:p-6">
        <Building2 className="h-6 w-6 text-accent" aria-hidden />
        <Field label="Business name" htmlFor="bp-name">
          <Input id="bp-name" required value={name} onChange={(e) => setName(e.target.value)} maxLength={200} placeholder="e.g. Rahman Textiles" />
          <FieldSource f={src("name")} dest="Business profile → name" />{offer("name")}
        </Field>
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="Place or area" htmlFor="bp-place" optional hint="Type it yourself; FlowSense never uses GPS or your IP address.">
            <Input id="bp-place" value={place} onChange={(e) => setPlace(e.target.value)} maxLength={200} placeholder="e.g. Mirpur, Dhaka" />
            <FieldSource f={src("place")} dest="Business profile → place" />{offer("place")}
          </Field>
          <Field label="Type of business" htmlFor="bp-type" optional>
            <Input id="bp-type" value={type} onChange={(e) => setType(e.target.value)} maxLength={100} placeholder="e.g. Garment workshop" />
            <FieldSource f={src("business_type")} dest="Business profile → type" />{offer("business_type")}
          </Field>
        </div>
        <Field label="Country or region" htmlFor="bp-country" optional hint="Only used to suggest currencies and timezones. It never selects them for you.">
          <Combobox id="bp-country" items={regionItems} value={country} onChange={setCountry} placeholder="Start typing, e.g. Bangladesh" />
        </Field>
        {country && <Button size="sm" variant="quiet" onClick={() => setCountry(null)}>Clear country</Button>}
        <Field
          label="Accounting currency"
          htmlFor="bp-currency"
          hint={locked ? `Records already exist in ${b.currency}, so the accounting currency can't change.` : "The currency your records, payments and reserve are kept in. It can differ from your country's currency (for example, USD books in Bangladesh)."}
        >
          {locked || fixture ? (
            <Input id="bp-currency" value={b.currency ?? ""} disabled readOnly />
          ) : (
            <Combobox id="bp-currency" items={items} value={currency} onChange={setCurrency} suggested={suggestedCurrencies} placeholder="Search by code or name, e.g. USD, BDT" />
          )}
        </Field>
        <Field
          label="Business timezone"
          htmlFor="bp-tz"
          hint={
            <>
              Deadlines such as payroll times use this timezone.
              {deviceTz && !tz && <> Your device is set to {deviceTz}; choose it below if that is where the business runs.</>}
              {zones && country && zones.suggested.length > 1 && <> This country has {zones.suggested.length} timezones: pick the right one.</>}
            </>
          }
        >
          {fixture ? (
            <Input id="bp-tz" value={b.timezone ?? ""} disabled readOnly />
          ) : (
            <Combobox id="bp-tz" items={zoneItems} value={tz} onChange={setTz} suggested={zones?.suggested} placeholder="Search, e.g. Dhaka, London, New York" />
          )}
        </Field>
        <details className="group rounded-lg border border-line px-3 py-2" open={!!read || Object.values(lic).some((v) => v) || undefined}>
          <summary className="flex min-h-8 cursor-pointer list-none items-center justify-between text-sm font-medium text-muted">Licence details (optional)</summary>
          <div className="grid gap-4 pt-3 sm:grid-cols-2">
            <Field label="Licence or registration number" htmlFor="bp-reg" optional>
              <Input id="bp-reg" value={lic.registration_number ?? ""} maxLength={80} onChange={(e) => setLic({ ...lic, registration_number: e.target.value })} placeholder="e.g. TRAD/045812/2026" />
              <FieldSource f={src("registration_number")} dest="Licence → number" />{offer("registration_number")}
            </Field>
            <Field label="Owner's name" htmlFor="bp-owner" optional>
              <Input id="bp-owner" value={lic.owner_name ?? ""} maxLength={120} onChange={(e) => setLic({ ...lic, owner_name: e.target.value })} />
              <FieldSource f={src("owner_name")} dest="Licence → owner" />{offer("owner_name")}
            </Field>
            <Field label="Issue date" htmlFor="bp-issue" optional>
              <Input id="bp-issue" type="date" value={lic.issue_date ?? ""} onChange={(e) => setLic({ ...lic, issue_date: e.target.value })} />
              <FieldSource f={src("issue_date")} dest="Licence → issue date" />{offer("issue_date")}
            </Field>
            <Field label="Expiry date" htmlFor="bp-expiry" optional>
              <Input id="bp-expiry" type="date" value={lic.expiry_date ?? ""} onChange={(e) => setLic({ ...lic, expiry_date: e.target.value })} />
              <FieldSource f={src("expiry_date")} dest="Licence → expiry date" />{offer("expiry_date")}
            </Field>
          </div>
        </details>
        <label className="flex items-start gap-2 rounded-lg bg-sunken p-3 text-sm">
          <input type="checkbox" required checked={confirm} onChange={(e) => setConfirm(e.target.checked)} className="mt-1" />
          <span>
            I confirm the accounting currency <strong>{currency ?? "…"}</strong> and business timezone <strong>{tz ?? "…"}</strong>.
            <span className="mt-0.5 flex gap-1 text-xs text-muted"><Info className="h-3.5 w-3.5 shrink-0" aria-hidden /> Changing the timezone later changes how deadlines are shown, not when they are due.</span>
          </span>
        </label>
        {err && <Notice tone="error">{err}</Notice>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => router.back()} disabled={busy}>Cancel</Button>
          <Button type="submit" busy={busy} disabled={me.role !== "owner" || !confirm}>Save business</Button>
        </div>
      </form>
    </div>
  );
}
