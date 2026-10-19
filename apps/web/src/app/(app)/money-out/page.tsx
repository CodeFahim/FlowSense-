"use client";

import { ListPresence, itemMotion, motion } from "@/components/motion";
import { AlertTriangle, Banknote, CalendarClock, Pencil, Plus, Repeat } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { recordsChanged, useRecordsChanged } from "@/components/records";
import { MoneyTabs } from "@/components/money-tabs";
import { useSession } from "@/components/shell";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, Empty, Field, Input, Loading, Notice, Segmented, type Tone } from "@/components/ui";
import { ApiError, apiGet, apiPatch, apiPost } from "@/lib/api";
import { dateTime, money } from "@/lib/format";
import { errorText } from "@/lib/outcomes";
import type { Obligation } from "@/lib/types";

const STATE: Record<Obligation["state"], { text: string; tone: Tone; help: string }> = {
  planned: { text: "Planned", tone: "neutral", help: "Recorded. Not paid, and no payment is being prepared." },
  needs_approval: { text: "Needs approval", tone: "warn", help: "A payment is prepared and waits for the owner's approval." },
  ready_to_send: { text: "Ready to send", tone: "accent", help: "Approved, not sent yet." },
  payment_in_progress: { text: "Payment in progress", tone: "info", help: "Sent to the sandbox bank; waiting for settlement." },
  paid: { text: "Paid", tone: "ok", help: "Fully paid." },
};

type Filter = "open" | "paid" | "all";

export default function MoneyOutPage() {
  const { me, openAdd } = useSession();
  const [list, setList] = useState<Obligation[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("open");
  const [edit, setEdit] = useState<string | null>(null);
  const load = useCallback(() => {
    apiGet<Obligation[]>("/api/obligations").then(setList, (e) => setErr(errorText(e, "Bills could not be loaded.")));
  }, []);
  useEffect(load, [load]);
  useRecordsChanged(load);

  if (err) return <Notice tone="error" title="Could not load Money out">{err}</Notice>;
  if (!list) return <Loading what="Loading bills" />;
  const cur = me.business.currency ?? undefined;
  const tz = me.business.timezone ?? "UTC";
  const shown = list.filter((o) => filter === "all" || (filter === "paid" ? o.state === "paid" : o.state !== "paid"));
  const unpaid = list.reduce((s, o) => s + o.unpaid_minor, 0);

  return (
    <div className="space-y-6">
      <MoneyTabs />
      <p className="text-sm text-muted">Bills, payroll and other money you spend. Recording a bill never pays it. For spending that already happened, choose “Already paid” when you add the expense, or open a bill and record its payment.</p>
      {list.length === 0 ? (
        <Card>
          <Empty icon={Banknote} title="No bills or payroll yet" action={<Button onClick={() => openAdd("bill")}><Plus className="h-4 w-4" aria-hidden /> Add your first bill</Button>}>
            Add what you must pay soon (rent, suppliers, wages) with the due date. {me.business.kind === "workspace" && <>If there is nothing to pay, mark that in the <Link href="/setup" className="font-medium text-accent-ink underline">setup checklist</Link>.</>}
          </Empty>
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted">Still to pay: <span className="num font-semibold text-ink">{money(unpaid, cur)}</span></p>
            <Segmented<Filter> label="Filter" value={filter} onChange={setFilter} options={[{ value: "open", label: "To pay" }, { value: "paid", label: "Paid" }, { value: "all", label: "All" }]} />
          </div>
          <ul className="space-y-3" aria-label="Bills and payroll">
            <ListPresence>{shown.map((o) => (
              <motion.li {...itemMotion} key={o.id}>
                <Card>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold">{o.description}</p>
                      <p className="text-sm text-muted">{o.payee?.name ?? "—"}{o.kind === "payroll" ? " · payroll" : ""}</p>
                      <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                        <CalendarClock className="h-3.5 w-3.5" aria-hidden /> Due {dateTime(o.due_at, tz)} ({tz})
                        {o.essential && <Badge tone="info">Essential</Badge>}
                        {o.recurrence !== "none" && <Badge icon={Repeat}>{o.recurrence === "weekly" ? "Weekly" : "Monthly"}</Badge>}
                        {o.overdue && <Badge tone="danger" icon={AlertTriangle}>Overdue</Badge>}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="num text-lg font-semibold">{money(o.unpaid_minor, cur)}</p>
                      {o.paid_minor > 0 && o.state !== "paid" && <p className="text-xs text-muted">{money(o.paid_minor, cur)} already paid</p>}
                      <span title={STATE[o.state].help}><Badge tone={STATE[o.state].tone}>{STATE[o.state].text}</Badge></span>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {(o.state === "needs_approval" || o.state === "ready_to_send") && <Link href="/approvals" className="inline-flex min-h-8 items-center rounded-lg bg-accent px-2.5 text-[0.8rem] font-medium text-on-accent">Open Approvals</Link>}
                    {o.state !== "paid" && o.state !== "payment_in_progress" && <Button size="sm" variant="quiet" onClick={() => setEdit(edit === o.id ? null : o.id)} aria-expanded={edit === o.id}><Pencil className="h-3.5 w-3.5" aria-hidden /> Edit</Button>}
                  </div>
                  {edit === o.id && <EditBill o={o} onDone={() => { setEdit(null); recordsChanged(); }} />}
                </Card>
              </motion.li>
            ))}</ListPresence>
          </ul>
        </>
      )}
    </div>
  );
}

function EditBill({ o, onDone }: { o: Obligation; onDone: () => void }) {
  const { me } = useSession();
  const cur = me.business.currency!;
  const tz = me.business.timezone ?? "UTC";
  const toast = useToast();
  const local = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(o.due_at));
  const [due, setDue] = useState(local);
  const [desc, setDesc] = useState(o.description);
  const [amount, setAmount] = useState("");
  const [essential, setEssential] = useState(o.essential);
  const [paid, setPaid] = useState("");
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: me.business.timezone ?? "UTC" }).format(new Date());
  const [paidOn, setPaidOn] = useState(today);
  // Fixed per paid amount and date: a retry or double click records the payment once.
  const [payKey, setPayKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy("save");
    setErr(null);
    try {
      const r = await apiPatch<{ revoked_approvals: string[] }>(`/api/obligations/${o.id}`, { due_on: due, description: desc, amount: amount || null, currency: cur, essential });
      toast({ tone: "ok", title: "Bill updated", text: r.revoked_approvals.length ? "An earlier approval for this bill was cancelled; it needs approving again." : "The plan was refreshed." });
      onDone();
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : "Not saved. Try again.");
    } finally {
      setBusy(null);
    }
  }
  async function markPaid() {
    setBusy("paid");
    setErr(null);
    try {
      const r = await apiPost<{ detail: string }>(`/api/obligations/${o.id}/payments`, { amount: paid, currency: cur, paid_on: paidOn, idempotency_key: payKey });
      toast({ tone: "ok", title: "Recorded as paid outside FlowSense", text: r.detail });
      onDone();
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : "Not saved. Try again.");
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="mt-4 space-y-4 rounded-xl border border-line bg-sunken p-4">
      <form onSubmit={save} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Due date" htmlFor={`d-${o.id}`}><Input id={`d-${o.id}`} type="date" value={due} onChange={(e) => setDue(e.target.value)} /></Field>
          <Field label={`New amount (${cur})`} htmlFor={`a-${o.id}`} optional hint={`Currently ${money(o.amount_minor, cur)}. Editing cancels approvals for this bill.`}>
            <Input id={`a-${o.id}`} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="Leave empty to keep" className="num" />
          </Field>
        </div>
        <Field label="Description" htmlFor={`s-${o.id}`}><Input id={`s-${o.id}`} value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={400} /></Field>
        {o.kind !== "payroll" && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={essential} onChange={(e) => setEssential(e.target.checked)} /> Essential payment (protected in the shortage calculation)</label>}
        <div className="flex justify-end"><Button type="submit" size="sm" busy={busy === "save"}>Save changes</Button></div>
      </form>
      {me.business.kind === "workspace" && me.role === "owner" && (
        <div className="space-y-2 border-t border-line pt-4">
          <h3 className="text-sm font-medium">Already paid outside FlowSense?</h3>
          <div className="flex flex-wrap items-end gap-2">
            <Field label={`Amount paid (${cur})`} htmlFor={`p-${o.id}`}>
              <Input id={`p-${o.id}`} inputMode="decimal" value={paid} onChange={(e) => { setPaid(e.target.value); setPayKey(crypto.randomUUID()); }} placeholder={money(o.unpaid_minor, cur)} className="num" />
            </Field>
            <Field label="Date paid" htmlFor={`pd-${o.id}`}>
              <Input id={`pd-${o.id}`} type="date" required value={paidOn} max={today} onChange={(e) => { setPaidOn(e.target.value); setPayKey(crypto.randomUUID()); }} />
            </Field>
            <Button variant="secondary" busy={busy === "paid"} disabled={!paid} onClick={markPaid}>Record as paid</Button>
          </div>
          <p className="text-xs text-muted">Cash goes down once by that amount, on the date paid. If you paid it before the date of the cash balance you entered, that balance already includes it and cash stays the same. A part payment leaves the rest unpaid. Recorded by you, not bank-verified.</p>
        </div>
      )}
      {err && <Notice tone="error">{err}</Notice>}
    </div>
  );
}
