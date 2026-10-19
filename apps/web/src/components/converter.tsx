"use client";

import { ArrowUpDown, Calculator, Globe2, Pencil, Sparkles } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { apiPost } from "@/lib/api";
import { dateTime, money } from "@/lib/format";
import { describeError } from "@/lib/outcomes";
import type { ConvertResult, RateSource } from "@/lib/types";
import { useSession } from "./shell";
import { Badge, Button, Combobox, Field, Input, Notice, Segmented, type ComboItem, type Tone } from "./ui";

type Mode = "latest" | "demo" | "manual";

export const RATE_STATUS: Record<RateSource["status"], { text: string; tone: Tone }> = {
  current: { text: "Latest published rate", tone: "ok" },
  cached: { text: "Cached rate", tone: "info" },
  stale: { text: "Out-of-date rate", tone: "warn" },
  demo: { text: "Demo rate (not market data)", tone: "warn" },
  manual: { text: "Manual rate (your figure)", tone: "warn" },
  unavailable: { text: "No rate available", tone: "danger" },
};

export function useCurrencyItems(): ComboItem[] {
  const { ref } = useSession();
  return useMemo(
    () => ref.currencies.map((c) => ({ value: c.code, label: c.code, hint: c.name, keywords: `${c.iso_name} ${c.used_in.join(" ")}` })),
    [ref.currencies],
  );
}

export function Converter() {
  const { me, savePrefs } = useSession();
  const items = useCurrencyItems();
  const p = me.preferences;
  const [amount, setAmount] = useState("");
  // Only the user's own confirmed currencies are prefilled; a fresh user picks both sides.
  const [from, setFrom] = useState<string | null>(p.convert_from ?? me.business.currency);
  const [to, setTo] = useState<string | null>(p.convert_to ?? (p.display_currency && p.display_currency !== me.business.currency ? p.display_currency : null));
  const [mode, setMode] = useState<Mode>("latest");
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ConvertResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function convert(e?: FormEvent, override?: Mode) {
    e?.preventDefault();
    if (!from || !to) {
      setErr("Choose both currencies.");
      return;
    }
    const m = override ?? mode;
    setBusy(true);
    setErr(null);
    try {
      setRes(await apiPost<ConvertResult>("/api/fx/convert", { amount: amount.trim(), from_currency: from, to_currency: to, mode: m, manual_rate: m === "manual" ? rate.trim() : undefined }));
    } catch (x) {
      const n = describeError(x);
      setErr(n.text ?? n.title);
      setRes(null);
    } finally {
      setBusy(false);
    }
  }

  function switchMode(m: Mode) {
    setMode(m);
    setRes(null);
    setErr(null);
  }

  const st = res ? RATE_STATUS[res.source.status] : null;

  return (
    <form onSubmit={convert} className="space-y-4 p-5">
      <p className="text-sm text-muted">For information only. Converting never moves money or changes any record.</p>
      <Field label="Amount" htmlFor="cv-amount">
        <Input id="cv-amount" inputMode="decimal" required value={amount} onChange={(e) => { setAmount(e.target.value); setRes(null); }} autoComplete="off" placeholder="e.g. 1,000" />
      </Field>
      <Field label="From" htmlFor="cv-from">
        <Combobox id="cv-from" items={items} value={from} onChange={(v) => { setFrom(v); setRes(null); }} placeholder="Search code, name or country" />
      </Field>
      <div className="flex justify-center">
        <Button variant="secondary" size="sm" onClick={() => { setFrom(to); setTo(from); setRes(null); }} aria-label="Swap currencies">
          <ArrowUpDown className="h-4 w-4" aria-hidden /> Swap
        </Button>
      </div>
      <Field label="To" htmlFor="cv-to">
        <Combobox id="cv-to" items={items} value={to} onChange={(v) => { setTo(v); setRes(null); }} placeholder="Search code, name or country" />
      </Field>
      <div className="space-y-1.5">
        <p className="text-sm font-medium">Rate source</p>
        <Segmented<Mode>
          label="Rate source"
          value={mode}
          onChange={switchMode}
          options={[
            { value: "latest", label: "Online", icon: Globe2 },
            { value: "manual", label: "Manual", icon: Pencil },
            { value: "demo", label: "Demo", icon: Sparkles },
          ]}
        />
        <p className="text-xs text-muted">
          {mode === "latest" && "The server's exchange-rate provider. Rates are published about once a day: a reference rate, not a real-time bank quote."}
          {mode === "manual" && "Type a rate you trust. It is labelled as manual, not verified, and never applied to your records."}
          {mode === "demo" && "Fixed illustrative values for demonstrations. Not market data."}
        </p>
      </div>
      {mode === "manual" && (
        <Field label={`Rate: 1 ${from ?? "…"} equals how many ${to ?? "…"}?`} htmlFor="cv-rate">
          <Input id="cv-rate" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} placeholder="e.g. 117.5" required />
        </Field>
      )}
      <Button type="submit" busy={busy} className="w-full"><Calculator className="h-4 w-4" aria-hidden /> Convert</Button>

      <div aria-live="polite" className="space-y-3">
        {err && <Notice tone="error" title="Could not convert">{err}</Notice>}
        {res && res.result_display && st && (
          <div className="rounded-xl border border-line bg-sunken p-4">
            {/* Two currencies on screen: a shared symbol ("$", "¥") gets its code; the full code is in the title. */}
            <p className="num text-sm text-muted" title={res.amount_display}>{money(res.amount_minor, res.from, { mixed: true })} =</p>
            <p className="num mt-0.5 text-2xl font-semibold tracking-[-0.01em] break-words" title={res.result_display}>{res.result_minor === null ? res.result_display : money(res.result_minor, res.to, { mixed: true })}</p>
            <p className="num mt-2 text-sm">1 {res.from} = {res.rate} {res.to} <span className="text-muted">· 1 {res.to} = {res.inverse_rate} {res.from}</span></p>
            <div className="mt-3 flex flex-wrap items-center gap-2"><Badge tone={st.tone}>{st.text}</Badge></div>
            <dl className="mt-2 space-y-0.5 text-xs text-muted">
              <div><dt className="inline">Source: </dt><dd className="inline">{res.source.label}</dd></div>
              {(res.source.provider_as_of || res.source.rate_date) && <div><dt className="inline">Published: </dt><dd className="inline">{res.source.provider_as_of ? dateTime(res.source.provider_as_of) : res.source.rate_date}</dd></div>}
              {res.source.fetched_at && <div><dt className="inline">Retrieved: </dt><dd className="inline">{dateTime(res.source.fetched_at)}</dd></div>}
              <div><dd>{res.source.detail}</dd></div>
              {res.derived && <div><dd>{res.derived}</dd></div>}
              {res.rounding && <div><dd>{res.rounding}</dd></div>}
              {res.source.fees_note && <div><dd>{res.source.fees_note}</dd></div>}
              {res.source.attribution && (
                <div><dd><a href={res.source.attribution.url} target="_blank" rel="noopener noreferrer" className="underline">{res.source.attribution.text}</a></dd></div>
              )}
            </dl>
          </div>
        )}
        {res && !res.result_display && (
          <Notice tone="warn" title={res.source.status === "unavailable" ? "No automatic rate available" : `No ${mode === "demo" ? "demo" : "automatic"} rate for ${res.missing.join(" and ")}`}>
            <p>{res.source.status === "unavailable" ? res.source.detail : `${mode === "demo" ? "The demo table" : res.source.label} does not quote ${res.missing.join(" and ")}. Nothing was estimated.`}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" onClick={() => switchMode("manual")}>Enter a manual rate</Button>
            </div>
          </Notice>
        )}
      </div>

      {from && to && (
        <div className="border-t border-line pt-3">
          <Button variant="ghost" size="sm" onClick={async () => { await savePrefs({ convert_from: from, convert_to: to }); setSaved(true); }}>Make {from} → {to} my default</Button>
          {saved && <span className="ml-2 text-xs text-ok" role="status">Saved</span>}
        </div>
      )}
    </form>
  );
}
