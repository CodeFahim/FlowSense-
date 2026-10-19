"use client";

import { ListPresence, itemMotion, motion } from "@/components/motion";
import { ArrowDownLeft, ArrowUpRight, Clock, History } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { MoneyTabs } from "@/components/money-tabs";
import { useRecordsChanged } from "@/components/records";
import { useSession } from "@/components/shell";
import { Badge, Card, Empty, Field, Input, Loading, Notice, Select, Stat } from "@/components/ui";
import { apiGet } from "@/lib/api";
import { dateOnly, money } from "@/lib/format";
import { errorText } from "@/lib/outcomes";

type Row = { id: string; date: string; description: string; category?: string; kind?: "receipt" | "payment" | "adjustment"; money_in_minor: number; money_out_minor: number; balance_minor?: number; source: string; status: string };
type Month = { month: string; opening_balance_minor: number; money_in_minor: number; money_out_minor: number; net_minor: number; adjustments_minor: number; closing_balance_minor: number; count: number };
type History = { currency: string; settled_balance_minor: number; balance_source: string; rows: Row[]; months: Month[]; pending: Row[]; expected: Row[]; categories: string[]; note: string };

function monthName(m: string): string {
  const [y, mo] = m.split("-").map(Number);
  return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
}

export default function MoneyHistoryPage() {
  const { me } = useSession();
  const [h, setH] = useState<History | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [direction, setDirection] = useState<"all" | "in" | "out">("all");
  const [category, setCategory] = useState("");

  const load = useCallback(async () => {
    if (!me.business.configured) return;
    const q = new URLSearchParams();
    if (from) q.set("from", from);
    if (to) q.set("to", to);
    if (direction !== "all") q.set("direction", direction);
    if (category) q.set("category", category);
    try {
      setH(await apiGet<History>(`/api/cashflow/history?${q.toString()}`));
      setErr(null);
    } catch (e) {
      setErr(errorText(e, "Cash flow history could not be loaded."));
    }
  }, [me.business.configured, from, to, direction, category]);
  useEffect(() => {
    void load();
  }, [load]);
  useRecordsChanged(load);

  const cur = h?.currency ?? me.business.currency ?? "";
  const thisMonth = h?.months[0];
  const filtered = from || to || direction !== "all" || category;
  const expectedIn = useMemo(() => (h?.expected ?? []).reduce((n, r) => n + r.money_in_minor, 0), [h]);
  const expectedOut = useMemo(() => (h?.expected ?? []).reduce((n, r) => n + r.money_out_minor, 0), [h]);

  if (!me.business.configured) {
    return (
      <div className="space-y-6">
        <MoneyTabs />
        <Notice tone="info" title="Set up your business first">Choose your currency and timezone, then everything you record shows up here automatically. <Link href="/setup/business" className="font-medium underline">Set up now</Link></Notice>
      </div>
    );
  }
  return (
    <div className="space-y-6">
      <MoneyTabs />
      {err && <Notice tone="error">{err}</Notice>}
      {!h ? <Loading what="Loading cash flow history" /> : (
        <>
          <section aria-label="Summary" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Settled cash balance" value={money(h.settled_balance_minor, cur)} sub={h.balance_source} tip="Settled money only. Balances you declare are your own figures; sandbox settlements are simulated. No real bank is connected." />
            <Stat label={thisMonth ? `Received in ${monthName(thisMonth.month)}` : "Received this month"} value={money(thisMonth?.money_in_minor ?? 0, cur)} sub={thisMonth?.adjustments_minor ? `Not counted: ${thisMonth.adjustments_minor < 0 ? "−" : "+"}${money(Math.abs(thisMonth.adjustments_minor), cur)} opening balance or correction (existing cash, not received)` : "Payments received only"} />
            <Stat label={thisMonth ? `Paid out in ${monthName(thisMonth.month)}` : "Paid out this month"} value={money(thisMonth?.money_out_minor ?? 0, cur)} sub="Payments made only" />
            <Stat label="Still expected" value={`+${money(expectedIn, cur)} / −${money(expectedOut, cur)}`} sub="Unpaid invoices / upcoming bills. Not cash yet." />
          </section>

          <Card title="Cash flow history" description="Made automatically from balances you declare, payments you record and sandbox settlements. Newest first.">
            <form className="mb-4 grid gap-3 sm:grid-cols-4" onSubmit={(e) => e.preventDefault()} aria-label="Filter history">
              <Field label="From" htmlFor="h-from"><Input id="h-from" type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} /></Field>
              <Field label="To" htmlFor="h-to"><Input id="h-to" type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} /></Field>
              <Field label="Show" htmlFor="h-dir">
                <Select id="h-dir" value={direction} onChange={(e) => setDirection(e.target.value as typeof direction)}>
                  <option value="all">Money in and out</option>
                  <option value="in">Money in only</option>
                  <option value="out">Money out only</option>
                </Select>
              </Field>
              <Field label="Category" htmlFor="h-cat">
                <Select id="h-cat" value={category} onChange={(e) => setCategory(e.target.value)}>
                  <option value="">All categories</option>
                  {h.categories.map((c) => <option key={c} value={c}>{c}</option>)}
                </Select>
              </Field>
            </form>
            {h.rows.length === 0 ? (
              <Empty icon={History} title={filtered ? "Nothing matches these filters" : "No money has moved yet"}>
                {filtered ? "Try a wider date range or another category." : "Record your available cash, or a payment you received or made, and it appears here with a running balance."}
              </Empty>
            ) : (
              <>
                <div className="hidden overflow-x-auto md:block">
                  <table className="w-full text-sm">
                    <thead className="text-left text-xs text-muted">
                      <tr><th className="py-2 pr-3 font-medium">Date</th><th className="py-2 pr-3 font-medium">Description</th><th className="py-2 pr-3 font-medium">Category</th><th className="py-2 pr-3 text-right font-medium">Money in</th><th className="py-2 pr-3 text-right font-medium">Money out</th><th className="py-2 pr-3 text-right font-medium">Balance</th><th className="py-2 font-medium">Source</th></tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {h.rows.map((r) => (
                        <tr key={r.id}>
                          <td className="py-2 pr-3 whitespace-nowrap">{dateOnly(r.date)}</td>
                          <td className="py-2 pr-3">{r.description}</td>
                          <td className="py-2 pr-3"><Badge tone="neutral">{r.category}</Badge></td>
                          <td className={`num py-2 pr-3 text-right ${r.kind === "adjustment" ? "text-muted" : "text-ok"}`}>{r.money_in_minor ? money(r.money_in_minor, cur) : ""}</td>
                          <td className="num py-2 pr-3 text-right">{r.money_out_minor ? money(r.money_out_minor, cur) : ""}</td>
                          <td className="num py-2 pr-3 text-right font-medium">{money(r.balance_minor ?? 0, cur)}</td>
                          <td className="py-2 text-xs text-muted">{r.source}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <ul className="divide-y divide-line md:hidden">
                  <ListPresence>{h.rows.map((r) => (
                    <motion.li {...itemMotion} key={r.id} className="flex items-start justify-between gap-3 py-3">
                      <div className="min-w-0">
                        <p className="font-medium break-words">{r.description}</p>
                        <p className="text-xs text-muted">{dateOnly(r.date)} · {r.category} · {r.source}</p>
                      </div>
                      <div className="shrink-0 text-right">
                        <p className={`num font-semibold ${r.money_in_minor && r.kind !== "adjustment" ? "text-ok" : ""}`}>{r.money_in_minor ? `+${money(r.money_in_minor, cur)}` : `−${money(r.money_out_minor, cur)}`}</p>
                        <p className="num text-xs text-muted">Balance {money(r.balance_minor ?? 0, cur)}</p>
                      </div>
                    </motion.li>
                  ))}</ListPresence>
                </ul>
              </>
            )}
            <p className="mt-3 text-xs text-muted">{h.note}</p>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Waiting for the bank" description="Payments sent but not confirmed yet. They don't change the balance until they settle.">
              {h.pending.length === 0 ? <Empty icon={Clock} title="Nothing in progress" /> : (
                <ul className="divide-y divide-line">{h.pending.map((r) => (
                  <li key={r.id} className="flex justify-between gap-3 py-2 text-sm"><span className="min-w-0">{r.description}</span><span className="num shrink-0">{money(r.money_in_minor || r.money_out_minor, cur)}</span></li>
                ))}</ul>
              )}
            </Card>
            <Card title="Expected (not cash yet)" description="Unpaid invoices and upcoming bills, by due date.">
              {h.expected.length === 0 ? <Empty title="Nothing expected" /> : (
                <ul className="divide-y divide-line">{h.expected.slice(0, 12).map((r) => (
                  <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                    <span className="flex min-w-0 items-center gap-2">
                      {r.money_in_minor ? <ArrowDownLeft className="h-4 w-4 shrink-0 text-ok" aria-label="Money in" /> : <ArrowUpRight className="h-4 w-4 shrink-0 text-warn" aria-label="Money out" />}
                      <span className="min-w-0"><span className="block truncate">{r.description}</span><span className="text-xs text-muted">Due {dateOnly(r.date)}</span></span>
                    </span>
                    <span className="num shrink-0">{money(r.money_in_minor || r.money_out_minor, cur)}</span>
                  </li>
                ))}</ul>
              )}
            </Card>
          </div>

          <Card title="Monthly summary" description="Settled money only. Declared balances and corrections are adjustments, not money received or spent. Opening + received − paid ± adjustments = closing.">
            {h.months.length === 0 ? <Empty title="No months yet" /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted"><tr><th className="py-2 pr-3 font-medium">Month</th><th className="py-2 pr-3 text-right font-medium">Opening</th><th className="py-2 pr-3 text-right font-medium">Received</th><th className="py-2 pr-3 text-right font-medium">Paid</th><th className="py-2 pr-3 text-right font-medium">Net</th><th className="py-2 pr-3 text-right font-medium">Adjustments</th><th className="py-2 text-right font-medium">Closing balance</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {h.months.map((m) => (
                      <tr key={m.month}>
                        <td className="py-2 pr-3 whitespace-nowrap">{monthName(m.month)}</td>
                        <td className="num py-2 pr-3 text-right">{money(m.opening_balance_minor, cur)}</td>
                        <td className="num py-2 pr-3 text-right">{money(m.money_in_minor, cur)}</td>
                        <td className="num py-2 pr-3 text-right">{money(m.money_out_minor, cur)}</td>
                        <td className={`num py-2 pr-3 text-right ${m.net_minor < 0 ? "text-danger" : "text-ok"}`}>{m.net_minor < 0 ? "−" : "+"}{money(Math.abs(m.net_minor), cur)}</td>
                        <td className="num py-2 pr-3 text-right text-muted">{m.adjustments_minor ? `${m.adjustments_minor < 0 ? "−" : "+"}${money(Math.abs(m.adjustments_minor), cur)}` : "—"}</td>
                        <td className="num py-2 text-right font-medium">{money(m.closing_balance_minor, cur)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
