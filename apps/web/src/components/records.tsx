"use client";

import { AnimatePresence, fast, motion, spring, useExitValue } from "./motion";
import { Banknote, Building2, ChevronDown, Plus, ReceiptText, Upload, Users, Wallet } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useSession } from "./shell";
import { useToast } from "./toast";
import { Button, Field, Input, Modal, Notice, Segmented, Select } from "./ui";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import { money } from "@/lib/format";
import type { Counterparty, Invoice } from "@/lib/types";

export type AddKind = "cash" | "invoice" | "bill" | "payroll" | "party" | "document";
export type Prefill = Partial<Record<"counterparty" | "description" | "amount" | "due_date" | "reference", string>>;

export const ADD_OPTIONS: { kind: AddKind; label: string; hint: string; Icon: typeof Plus }[] = [
  { kind: "cash", label: "Available cash", hint: "Money already in your account", Icon: Wallet },
  { kind: "invoice", label: "Customer invoice", hint: "Money a customer owes you", Icon: ReceiptText },
  { kind: "bill", label: "Expense or bill", hint: "Money you spent or need to pay", Icon: Banknote },
  { kind: "payroll", label: "Payroll or wages", hint: "Recurring staff payments", Icon: Users },
  { kind: "party", label: "Customer or supplier", hint: "Someone you trade with", Icon: Building2 },
  { kind: "document", label: "Supporting document", hint: "PDF or note for an invoice", Icon: Upload },
];

export const RECORDS_CHANGED = "flowsense:records-changed";
export function recordsChanged() {
  window.dispatchEvent(new Event(RECORDS_CHANGED));
}
/** Re-run `load` whenever any form saves a record (Home, lists, forecast and the assistant refresh). */
export function useRecordsChanged(load: () => void) {
  const ref = useRef(load);
  useEffect(() => {
    ref.current = load;
  }, [load]);
  useEffect(() => {
    const h = () => ref.current();
    window.addEventListener(RECORDS_CHANGED, h);
    return () => window.removeEventListener(RECORDS_CHANGED, h);
  }, []);
}

function businessToday(sandboxNow: string | null, tz: string | null): string {
  const d = sandboxNow ? new Date(sandboxNow) : new Date();
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz ?? "UTC", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

function useParties(kind: "customer" | "supplier" | "payroll") {
  const [list, setList] = useState<Counterparty[]>([]);
  useEffect(() => {
    apiGet<{ counterparties: Counterparty[] }>("/api/counterparties").then((r) => setList(r.counterparties.filter((c) => c.kind === kind)), () => undefined);
  }, [kind]);
  return list;
}

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.reasons.length ? `${e.message} ${e.reasons.map((r) => r.message).join(" ")}` : e.message;
  return "FlowSense could not be reached. Your entries are kept; try again.";
}

function MoneyField({ id, label, value, onChange, currency, hint, allowZero }: { id: string; label: string; value: string; onChange: (v: string) => void; currency: string; hint?: ReactNode; allowZero?: boolean }) {
  return (
    <Field label={`${label} (${currency})`} htmlFor={id} hint={hint}>
      <div className="relative">
        <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-sm font-medium text-muted">{currency}</span>
        <Input id={id} inputMode="decimal" autoComplete="off" required value={value} onChange={(e) => onChange(e.target.value)} placeholder={allowZero ? "0.00" : "1,250.00"} className="pl-14 num" />
      </div>
    </Field>
  );
}

function Optional({ children, label = "More details (optional)" }: { children: ReactNode; label?: string }) {
  return (
    <details className="group rounded-lg border border-line px-3 py-2">
      <summary className="flex min-h-8 cursor-pointer list-none items-center justify-between text-sm font-medium text-muted">
        {label} <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" aria-hidden />
      </summary>
      <div className="space-y-4 pt-3">{children}</div>
    </details>
  );
}

// A refused save nudges the button (with the error text shown above it); success closes the form with a toast.
function FormFooter({ busy, failed, onCancel, label }: { busy: boolean; failed?: boolean; onCancel: () => void; label: string }) {
  return (
    <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-4">
      <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
      <Button type="submit" busy={busy} feedback={failed && !busy ? "error" : null}>{label}</Button>
    </div>
  );
}

function NotReady({ onClose }: { onClose: () => void }) {
  return (
    <div className="space-y-4">
      <Notice tone="info" title="Set up your business first">Confirm your accounting currency and business timezone, so every amount and due date is recorded correctly.</Notice>
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Link href="/setup/business" onClick={onClose} className="inline-flex min-h-10 items-center rounded-lg bg-accent px-3.5 text-sm font-medium text-on-accent">Set up business</Link>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------- forms

function CashForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const { me, reload } = useSession();
  const cur = me.business.currency!;
  const today = businessToday(me.business.sandbox_now, me.business.timezone);
  const [amount, setAmount] = useState("");
  const [asOf, setAsOf] = useState(today);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();
  if (me.business.kind !== "workspace") return <Notice tone="info">Demo and sample workspaces have fixed sandbox balances. Use Demo scenarios to reset them.</Notice>;
  if (me.role !== "owner") return <Notice tone="info">Only the owner can record the cash balance.</Notice>;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!amount.trim()) {
      setErr(`Enter the amount of money in your account today, in ${cur}. Enter 0 if there is none.`);
      document.getElementById("cash-amount")?.focus();
      return;
    }
    if (!asOf) {
      setErr("Choose the date of this balance.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await apiPost<{ detail: string }>("/api/cash/declare", { amount, currency: cur, as_of: asOf, account_label: label || null });
      toast({ tone: "ok", title: "Available cash saved", text: r.detail });
      await reload();
      onDone();
    } catch (e2) {
      setErr(errText(e2));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <p className="text-sm text-muted">Money already in your business account(s) today. Enter 0 if there is none: zero is different from &quot;not entered&quot;. This is your own figure, not a bank-verified balance.</p>
      <MoneyField id="cash-amount" label="Amount available" value={amount} onChange={setAmount} currency={cur} allowZero />
      <Field label="Balance on date" htmlFor="cash-date" hint={`Business date in ${me.business.timezone}. Cannot be in the future.`}>
        <Input id="cash-date" type="date" required value={asOf} max={today} onChange={(e) => setAsOf(e.target.value)} />
      </Field>
      <Optional>
        <Field label="Account name" htmlFor="cash-label" optional>
          <Input id="cash-label" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={100} placeholder="e.g. Main business account" />
        </Field>
      </Optional>
      {err && <Notice tone="error">{err}</Notice>}
      <FormFooter busy={busy} failed={!!err} onCancel={onCancel} label="Save cash" />
    </form>
  );
}

function InvoiceForm({ prefill, onDone, onCancel }: { prefill?: Prefill; onDone: () => void; onCancel: () => void }) {
  const { me } = useSession();
  const cur = me.business.currency!;
  const today = businessToday(me.business.sandbox_now, me.business.timezone);
  const customers = useParties("customer");
  const [customer, setCustomer] = useState(prefill?.counterparty ?? "");
  const [number, setNumber] = useState(prefill?.reference ?? "");
  const [amount, setAmount] = useState(prefill?.amount ?? "");
  const [issued, setIssued] = useState(today);
  const [due, setDue] = useState(prefill?.due_date ?? "");
  const [description, setDescription] = useState(prefill?.description ?? "");
  const [partial, setPartial] = useState(true);
  const [days, setDays] = useState("7");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();
  const match = customers.find((c) => c.name.toLowerCase() === customer.trim().toLowerCase());

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const inv = await apiPost<Invoice>("/api/invoices", {
        customer_id: match?.id ?? null,
        new_customer: match ? null : customer.trim(),
        number,
        description,
        amount,
        currency: cur,
        issued_on: issued,
        due_on: due,
        accepts_partial: partial,
        typical_days_to_pay: Number(days) || 0,
      });
      toast({ tone: "ok", title: `Invoice ${inv.number} saved`, text: `${money(inv.amount_minor, cur)} from ${inv.customer?.name}. It counts as cash only when it is paid.` });
      onDone();
    } catch (e2) {
      setErr(errText(e2));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Customer" htmlFor="inv-customer" hint={match ? "Existing customer" : customer ? "A new customer will be added" : undefined}>
        <Input id="inv-customer" list="inv-customers" required value={customer} onChange={(e) => setCustomer(e.target.value)} maxLength={200} placeholder="e.g. Dhaka Fabrics Ltd" />
        <datalist id="inv-customers">{customers.map((c) => <option key={c.id} value={c.name} />)}</datalist>
      </Field>
      <MoneyField id="inv-amount" label="Amount" value={amount} onChange={setAmount} currency={cur} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Invoice number" htmlFor="inv-number">
          <Input id="inv-number" required value={number} onChange={(e) => setNumber(e.target.value)} maxLength={64} placeholder="e.g. INV-101" />
        </Field>
        <Field label="Due date" htmlFor="inv-due">
          <Input id="inv-due" type="date" required value={due} min={issued} onChange={(e) => setDue(e.target.value)} />
        </Field>
      </div>
      <Optional>
        <Field label="Issue date" htmlFor="inv-issued">
          <Input id="inv-issued" type="date" required value={issued} onChange={(e) => setIssued(e.target.value)} />
        </Field>
        <Field label="What it's for" htmlFor="inv-desc" optional>
          <Input id="inv-desc" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} placeholder="e.g. 200 shirts, September order" />
        </Field>
        {!match && (
          <>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={partial} onChange={(e) => setPartial(e.target.checked)} className="mt-1" />
              <span>This customer can be asked to pay part of the invoice early</span>
            </label>
            <Field label="Usual days to pay after a request" htmlFor="inv-days" hint="Used only for forecasts and plan timing.">
              <Input id="inv-days" type="number" min={0} max={120} value={days} onChange={(e) => setDays(e.target.value)} />
            </Field>
          </>
        )}
      </Optional>
      {err && <Notice tone="error">{err}</Notice>}
      <p className="text-xs text-muted">Add a PDF or note as evidence after saving, from Money in.</p>
      <FormFooter busy={busy} failed={!!err} onCancel={onCancel} label="Save invoice" />
    </form>
  );
}

function BillForm({ payroll, prefill, onDone, onCancel }: { payroll: boolean; prefill?: Prefill; onDone: () => void; onCancel: () => void }) {
  const { me } = useSession();
  const cur = me.business.currency!;
  const payees = useParties(payroll ? "payroll" : "supplier");
  const [payee, setPayee] = useState(prefill?.counterparty ?? "");
  const [description, setDescription] = useState(prefill?.description ?? (payroll ? "Staff wages" : ""));
  const [amount, setAmount] = useState(prefill?.amount ?? "");
  const [due, setDue] = useState(prefill?.due_date ?? "");
  const [time, setTime] = useState("17:00");
  const [kind, setKind] = useState<"supplier" | "other">("supplier");
  const [essential, setEssential] = useState(false);
  const [recurrence, setRecurrence] = useState<"none" | "weekly" | "monthly">(payroll ? "monthly" : "none");
  // First question for an expense: was it already paid (money out now) or is it a bill to pay later?
  const canMarkPaid = !payroll && me.business.kind === "workspace" && me.role === "owner";
  const [mode, setMode] = useState<"later" | "paid">("later");
  const paid = canMarkPaid && mode === "paid";
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: me.business.timezone ?? "UTC" }).format(new Date());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // One key per form: a retried or double-submitted payment is recorded once by the server.
  const [payKey] = useState(() => crypto.randomUUID());
  const toast = useToast();
  const match = payees.find((c) => c.name.toLowerCase() === payee.trim().toLowerCase());

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await apiPost<{ created: { id: string; description: string }[] }>("/api/obligations", {
        kind: payroll ? "payroll" : kind,
        payee_id: match?.id ?? null,
        new_payee: match ? null : payee.trim(),
        description,
        amount,
        currency: cur,
        due_on: due,
        due_time: `${time}:00`,
        essential: !paid && (payroll || essential),
        recurrence: paid ? "none" : recurrence,
      });
      if (paid) {
        // An expense already paid outside FlowSense: recorded as money out (owner's statement, not bank-verified).
        try {
          const p = await apiPost<{ detail: string }>(`/api/obligations/${r.created[0].id}/payments`, { amount, currency: cur, paid_on: due, idempotency_key: payKey });
          toast({ tone: "ok", title: "Expense recorded as paid", text: p.detail });
        } catch (e3) {
          toast({ tone: "warn", title: "Expense saved, payment not recorded", text: `${errText(e3)} You can record the payment later in Money out.` });
        }
      } else {
        toast({ tone: "ok", title: payroll ? "Payroll saved" : "Bill saved", text: `${r.created.length > 1 ? `${r.created.length} dated payments were added. ` : ""}Nothing is paid until you approve a payment.` });
      }
      onDone();
    } catch (e2) {
      setErr(errText(e2));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      {!payroll && me.business.kind !== "workspace" && (
        <p className="text-xs text-muted" data-testid="expense-sample-note">
          Sample workspace: cash changes only through the sandbox bank, so this saves an unpaid bill. Pay it from Approvals to see cash go down.
          &ldquo;Already paid&rdquo; is available in your own business workspace.
        </p>
      )}
      {canMarkPaid && (
        <div className="space-y-1.5">
          <Segmented<"later" | "paid">
            label="Has this expense been paid?"
            value={mode}
            onChange={setMode}
            options={[
              { value: "paid", label: "Already paid" },
              { value: "later", label: "Pay later (unpaid bill)" },
            ]}
          />
          <p className="text-xs text-muted" data-testid="expense-effect">
            {paid
              ? "Records money that already left: your recorded cash goes down by this amount once. Your own record, not checked by a bank."
              : "A bill you still have to pay: your cash doesn't change now. When you pay it, mark it paid in Money out (it is counted once)."}
          </p>
        </div>
      )}
      <Field label={payroll ? "Paid to" : paid ? "Who you paid" : "Who you pay"} htmlFor="bill-payee" hint={match ? "Existing payee" : payee ? "A new payee will be added (unverified sandbox account)" : undefined}>
        <Input id="bill-payee" list="bill-payees" required value={payee} onChange={(e) => setPayee(e.target.value)} maxLength={200} placeholder={payroll ? "e.g. Staff wages account" : "e.g. Landlord, Packaging supplier"} />
        <datalist id="bill-payees">{payees.map((c) => <option key={c.id} value={c.name} />)}</datalist>
      </Field>
      <Field label="What it's for" htmlFor="bill-desc">
        <Input id="bill-desc" required value={description} onChange={(e) => setDescription(e.target.value)} maxLength={400} placeholder={payroll ? "e.g. October wages" : "e.g. October rent"} />
      </Field>
      <MoneyField id="bill-amount" label="Amount" value={amount} onChange={setAmount} currency={cur} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={paid ? "Date paid" : "Due date"} htmlFor="bill-due" hint={paid ? "Counted in cash history on this date. If it was paid before the date of the cash balance you entered, that balance already includes it, so cash won't go down again." : undefined}>
          <Input id="bill-due" type="date" required value={due} max={paid ? today : undefined} onChange={(e) => setDue(e.target.value)} />
        </Field>
        {!paid && <Field label="Repeats" htmlFor="bill-rec">
          <Select id="bill-rec" value={recurrence} onChange={(e) => setRecurrence(e.target.value as typeof recurrence)}>
            <option value="none">Does not repeat</option>
            <option value="weekly">Every week (next 13)</option>
            <option value="monthly">Every month (next 6)</option>
          </Select>
        </Field>}
      </div>
      {paid ? null : payroll ? (
        <p className="text-xs text-muted">Payroll is always treated as essential: FlowSense protects it before other payments.</p>
      ) : (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={essential} onChange={(e) => setEssential(e.target.checked)} className="mt-1" />
          <span><span className="font-medium">Essential payment</span>
            <span className="block text-xs text-muted">Must be paid on time (for example rent, taxes or loan repayments). Essential payments are protected in the shortfall calculation.</span>
          </span>
        </label>
      )}
      <Optional>
        <Field label={`Due time (${me.business.timezone})`} htmlFor="bill-time">
          <Input id="bill-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </Field>
        {!payroll && (
          <Field label="Type" htmlFor="bill-kind">
            <Select id="bill-kind" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
              <option value="supplier">Supplier bill</option>
              <option value="other">Other payment</option>
            </Select>
          </Field>
        )}
      </Optional>
      {err && <Notice tone="error">{err}</Notice>}
      <FormFooter busy={busy} failed={!!err} onCancel={onCancel} label={payroll ? "Save payroll" : paid ? "Save paid expense" : "Save bill"} />
    </form>
  );
}

function PartyForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [kind, setKind] = useState<"customer" | "supplier">("customer");
  const [name, setName] = useState("");
  const [partial, setPartial] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const cp = await apiPost<Counterparty>("/api/parties", { kind, name, accepts_partial: kind === "customer" && partial });
      toast({ tone: "ok", title: `${cp.name} saved`, text: kind === "supplier" ? "Its sandbox account is unverified until the owner verifies it." : undefined });
      onDone();
    } catch (e2) {
      setErr(errText(e2));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Type" htmlFor="party-kind">
        <Select id="party-kind" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
          <option value="customer">Customer (pays you)</option>
          <option value="supplier">Supplier (you pay them)</option>
        </Select>
      </Field>
      <Field label="Name" htmlFor="party-name">
        <Input id="party-name" required value={name} onChange={(e) => setName(e.target.value)} maxLength={200} placeholder="e.g. Hotel Rosa" />
      </Field>
      {kind === "customer" && (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={partial} onChange={(e) => setPartial(e.target.checked)} className="mt-1" />
          <span>Can be asked to pay part of an invoice early</span>
        </label>
      )}
      {err && <Notice tone="error">{err}</Notice>}
      <FormFooter busy={busy} failed={!!err} onCancel={onCancel} label="Save" />
    </form>
  );
}

function DocumentPicker({ onCancel }: { onCancel: () => void }) {
  const router = useRouter();
  const [list, setList] = useState<Invoice[] | null>(null);
  const [sel, setSel] = useState("");
  useEffect(() => {
    apiGet<Invoice[]>("/api/invoices").then((l) => {
      setList(l);
      if (l[0]) setSel(l[0].id);
    }, () => setList([]));
  }, []);
  if (!list) return <p className="text-sm text-muted">Loading invoices…</p>;
  if (list.length === 0) {
    return (
      <div className="space-y-4">
        <Notice tone="info">Documents are attached to an invoice. Add the invoice first, then upload its PDF or a note.</Notice>
        <div className="flex justify-end"><Button variant="secondary" onClick={onCancel}>Close</Button></div>
      </div>
    );
  }
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onCancel();
        router.push(`/money-in?invoice=${sel}#evidence`);
      }}
      className="space-y-4"
    >
      <Field label="Attach to invoice" htmlFor="doc-invoice">
        <Select id="doc-invoice" value={sel} onChange={(e) => setSel(e.target.value)}>
          {list.map((i) => <option key={i.id} value={i.id}>{i.number} · {i.customer?.name}</option>)}
        </Select>
      </Field>
      <p className="text-sm text-muted">PDF (up to 5 MB, 20 pages, with its own text; for a scan use “Add from a document”, which reads scans) or a typed note. You review the extracted text before it is saved.</p>
      <FormFooter busy={false} onCancel={onCancel} label="Continue" />
    </form>
  );
}

const TITLES: Record<AddKind, string> = {
  cash: "Add available cash",
  invoice: "Add a customer invoice",
  bill: "Add an expense or bill",
  payroll: "Add payroll",
  party: "Add a customer or supplier",
  document: "Add a supporting document",
};

export function AddDialog({ kind, prefill, onClose }: { kind: AddKind | null; prefill?: Prefill; onClose: () => void }) {
  const { me, reload } = useSession();
  const done = () => {
    recordsChanged();
    void reload();
    onClose();
  };
  // While the dialog animates closed it keeps showing the form it had.
  const shown = useExitValue(kind);
  const needsConfig = shown !== null && shown !== "document" && !me.business.configured;
  return (
    <Modal open={kind !== null} onClose={onClose} title={shown ? TITLES[shown] : ""}>
      {shown && (needsConfig ? <NotReady onClose={onClose} /> : (
        <>
          {shown === "cash" && <CashForm onDone={done} onCancel={onClose} />}
          {shown === "invoice" && <InvoiceForm prefill={prefill} onDone={done} onCancel={onClose} />}
          {shown === "bill" && <BillForm payroll={false} prefill={prefill} onDone={done} onCancel={onClose} />}
          {shown === "payroll" && <BillForm payroll prefill={prefill} onDone={done} onCancel={onClose} />}
          {shown === "party" && <PartyForm onDone={done} onCancel={onClose} />}
          {shown === "document" && <DocumentPicker onCancel={onClose} />}
        </>
      ))}
    </Modal>
  );
}

/** The "+ Add" button and its menu. Keyboard: Enter/Space opens, arrows move, Escape closes. */
export function AddMenu({ onPick }: { onPick: (k: AddKind) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const items = useMemo(() => ADD_OPTIONS, []);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    box.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  function onKey(e: React.KeyboardEvent<HTMLDivElement>) {
    if (!open || (e.key !== "ArrowDown" && e.key !== "ArrowUp")) return;
    e.preventDefault();
    const els = Array.from(box.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? []);
    const i = els.indexOf(document.activeElement as HTMLButtonElement);
    els[(i + (e.key === "ArrowDown" ? 1 : els.length - 1)) % els.length]?.focus();
  }
  return (
    <div className="relative" ref={box} onKeyDown={onKey}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-accent px-3 text-sm font-medium text-on-accent hover:bg-accent-hover"
      >
        <Plus className="h-4 w-4" aria-hidden /> Add
      </button>
      <AnimatePresence>
      {open && (
        <motion.div key="menu" initial={{ opacity: 0, y: -6, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -6, scale: 0.97, transition: fast }} transition={spring} style={{ transformOrigin: "top right" }} role="menu" aria-label="Add" className="glass glass-strong absolute top-12 right-0 z-40 w-72 rounded-xl border border-line p-1.5 shadow-pop">
          {items.map(({ kind, label, hint, Icon }) => (
            <button
              key={kind}
              role="menuitem"
              type="button"
              onClick={() => {
                setOpen(false);
                onPick(kind);
              }}
              className="flex w-full items-start gap-3 rounded-lg px-3 py-2 text-left hover:bg-sunken focus-visible:bg-sunken"
            >
              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{label}</span>
                <span className="block text-xs text-muted">{hint}</span>
              </span>
            </button>
          ))}
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  );
}


