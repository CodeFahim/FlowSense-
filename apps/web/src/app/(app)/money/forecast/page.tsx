"use client";

import { LineChart } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { MoneyTabs } from "@/components/money-tabs";
import { useRecordsChanged } from "@/components/records";
import { useSession } from "@/components/shell";
import { Badge, Button, Card, Empty, Field, Input, Loading, Notice, Stat } from "@/components/ui";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import { money } from "@/lib/format";
import { errorText } from "@/lib/outcomes";

type MonthRow = { month: string; low_minor: number; base_minor: number; high_minor: number };
type Forecast = {
  status: "estimated" | "illustrative" | "needs_assumptions";
  currency: string;
  definition: string;
  method?: string;
  assumptions?: string[];
  history: { month: string; sales_minor: number }[];
  months?: MonthRow[];
  annual?: { low_minor: number; base_minor: number; high_minor: number };
  message?: string;
  disclaimer?: string;
};

function monthName(m: string): string {
  const [y, mo] = m.split("-").map(Number);
  return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });
}

/** Optional: a 12-month turnover (sales revenue) estimate. Never cash, never profit, never a promise. */
export default function ForecastPage() {
  const { me } = useSession();
  const [f, setF] = useState<Forecast | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sales, setSales] = useState("");
  const [growth, setGrowth] = useState("0");
  const [busy, setBusy] = useState(false);
  const [fieldErr, setFieldErr] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!me.business.configured) return;
    try {
      setF(await apiGet<Forecast>("/api/forecast/turnover"));
      setErr(null);
    } catch (e) {
      setErr(errorText(e, "The forecast could not be loaded."));
    }
  }, [me.business.configured]);
  useEffect(() => {
    void load();
  }, [load]);
  useRecordsChanged(load);

  async function illustrate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFieldErr(null);
    try {
      setF(await apiPost<Forecast>("/api/forecast/turnover", { expected_monthly_sales: sales, yearly_growth_pct: Number(growth || 0) }));
    } catch (e2) {
      setFieldErr(e2 instanceof ApiError ? e2.message : "FlowSense could not be reached. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const cur = f?.currency ?? me.business.currency ?? "";
  const peak = Math.max(1, ...(f?.months ?? []).map((m) => m.high_minor));
  return (
    <div className="space-y-6">
      <MoneyTabs />
      {!me.business.configured ? <Notice tone="info">Set up your business first.</Notice> : err ? <Notice tone="error">{err}</Notice> : !f ? <Loading what="Loading the forecast" /> : (
        <>
          <Notice tone="info" title="What this shows">{f.definition}</Notice>
          {(f.status === "needs_assumptions" || f.status === "illustrative") && (
            <Card title={f.status === "needs_assumptions" ? "Not enough sales history yet" : "Try different assumptions"} description={f.message ?? "Change the numbers to see another illustration."}>
              <form onSubmit={illustrate} className="grid gap-4 sm:grid-cols-3 sm:items-end">
                <Field label={`Expected sales a month (${cur})`} htmlFor="fc-sales" hint="e.g. 8,000" error={fieldErr}>
                  <Input id="fc-sales" inputMode="decimal" required value={sales} onChange={(e) => setSales(e.target.value)} placeholder="8,000" />
                </Field>
                <Field label="Expected growth a year (%)" htmlFor="fc-growth" hint="Effective yearly rate: 12 means month 12 is 12% above the starting month; −50 means it is half. Use 0 if unsure; −50 to 200.">
                  <Input id="fc-growth" type="number" min={-50} max={200} step="0.5" value={growth} onChange={(e) => setGrowth(e.target.value)} />
                </Field>
                <Button type="submit" busy={busy}>Show illustration</Button>
              </form>
            </Card>
          )}
          {f.months && f.annual && (
            <>
              <section aria-label="Next 12 months" className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <Stat label="Next 12 months: low" value={money(f.annual.low_minor, cur)} />
                <Stat label="Next 12 months: most likely" value={money(f.annual.base_minor, cur)} />
                <Stat label="Next 12 months: high" value={money(f.annual.high_minor, cur)} />
              </section>
              <Card
                title="Monthly turnover estimate"
                actions={<Badge tone={f.status === "estimated" ? "info" : "warn"}>{f.status === "estimated" ? "From your sales history" : "Illustrative (your assumptions)"}</Badge>}
                description={f.disclaimer}
              >
                <ul className="space-y-2" aria-label="Monthly estimates">
                  {f.months.map((m) => (
                    <li key={m.month} className="grid grid-cols-[5.5rem_1fr_auto] items-center gap-3 text-sm">
                      <span className="text-muted">{monthName(m.month)}</span>
                      <span className="relative h-3 rounded-full bg-sunken" aria-hidden>
                        <span className="animate-bar absolute inset-y-0 rounded-full bg-accent-soft" style={{ left: `${(m.low_minor / peak) * 100}%`, width: `${((m.high_minor - m.low_minor) / peak) * 100}%` }} />
                        <span className="absolute inset-y-0 w-1 rounded-full bg-accent" style={{ left: `calc(${(m.base_minor / peak) * 100}% - 2px)` }} />
                      </span>
                      <span className="num text-right"><span className="font-medium">{money(m.base_minor, cur)}</span> <span className="text-xs text-muted">({money(m.low_minor, cur)}–{money(m.high_minor, cur)})</span></span>
                    </li>
                  ))}
                </ul>
              </Card>
              <Card title="How it was calculated">
                {f.method && <p className="text-sm">{f.method}</p>}
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">{(f.assumptions ?? []).map((a) => <li key={a}>{a}</li>)}</ul>
              </Card>
            </>
          )}
          {f.history.length > 0 && (
            <Card title="Your invoiced sales by month">
              <ul className="grid gap-1 text-sm sm:grid-cols-2">{f.history.map((h) => <li key={h.month} className="flex justify-between gap-3"><span className="text-muted">{monthName(h.month)}</span><span className="num">{money(h.sales_minor, cur)}</span></li>)}</ul>
            </Card>
          )}
          {f.history.length === 0 && f.status !== "illustrative" && <Empty icon={LineChart} title="No invoiced sales yet">Add invoices under Money in, or use your own assumptions above.</Empty>}
        </>
      )}
    </div>
  );
}
