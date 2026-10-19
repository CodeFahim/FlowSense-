"use client";

import { ListPresence, Reveal, itemMotion, motion, useExitValue } from "@/components/motion";
import { FileUp, Mail, Pencil, Phone, Plus, UsersRound } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { DocReader, DocTypeBadge, FieldSource, SuggestionNotes, type Suggestion } from "@/components/doc-reader";
import { recordsChanged, useRecordsChanged } from "@/components/records";
import { useSession } from "@/components/shell";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, Empty, Field, Input, Loading, Modal, Notice, PageHeader } from "@/components/ui";
import { ApiError, apiGet, apiPatch, apiPost } from "@/lib/api";
import { dateOnly, money } from "@/lib/format";
import { errorText } from "@/lib/outcomes";

type Customer = {
  id: string; name: string; contact: { email?: string; phone?: string }; typical_days_to_pay: number;
  invoices: number; open_invoices: number; owed_minor: number; overdue_minor: number; next_due: string | null; oldest_overdue?: string | null; last_invoice: string | null; from_document: boolean;
};
type List = { currency: string | null; customers: Customer[]; total_owed_minor: number };

function fieldErrors(e: unknown): Record<string, string> {
  return e instanceof ApiError && e.body.fields ? (e.body.fields as Record<string, string>) : {};
}

export default function CustomersPage() {
  const { me } = useSession();
  const [list, setList] = useState<List | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState<"manual" | "document" | null>(null);
  const [editing, setEditing] = useState<Customer | null>(null);
  // The dialogs keep their content while they animate closed.
  const addingShown = useExitValue(adding);
  const editingShown = useExitValue(editing);
  const load = useCallback(async () => {
    try {
      setList(await apiGet<List>("/api/customers"));
      setErr(null);
    } catch (e) {
      setErr(errorText(e, "Customers could not be loaded."));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  useRecordsChanged(load);
  const cur = list?.currency ?? me.business.currency ?? "";
  const ready = me.business.configured;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Customers"
        description="The people and businesses who buy from you, and what they still owe. Money owed isn't cash until it's paid."
        actions={ready ? (
          <>
            <Button variant="secondary" onClick={() => setAdding("document")}><FileUp className="h-4 w-4" aria-hidden /> Add from a document</Button>
            <Button onClick={() => setAdding("manual")}><Plus className="h-4 w-4" aria-hidden /> Add customer</Button>
          </>
        ) : undefined}
      />
      {!ready && <Notice tone="info" title="Set up your business first">Choose your currency and timezone, then add customers. <Link href="/setup/business" className="font-medium underline">Set up now</Link></Notice>}
      {err && <Notice tone="error">{err}</Notice>}
      {ready && !list && !err && <Loading what="Loading customers" />}
      {list && list.customers.length === 0 && (
        <Empty icon={UsersRound} title="No customers yet" action={
          <div className="flex flex-wrap justify-center gap-2">
            <Button onClick={() => setAdding("manual")}><Plus className="h-4 w-4" aria-hidden /> Add customer</Button>
            <Button variant="secondary" onClick={() => setAdding("document")}><FileUp className="h-4 w-4" aria-hidden /> Add from an invoice photo or file</Button>
          </div>
        }>
          Add the customers who owe you money, for example &ldquo;Hotel Rosa&rdquo;. You can type them, or upload an invoice and FlowSense fills in the details for you to check.
        </Empty>
      )}
      {list && list.customers.length > 0 && (
        <>
          <p className="text-sm">Customers owe you <strong className="num">{money(list.total_owed_minor, cur)}</strong> in total.</p>
          <ul className="grid gap-3 lg:grid-cols-2">
            <ListPresence>{list.customers.map((c) => (
              <motion.li {...itemMotion} key={c.id}>
                <Card>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold break-words">{c.name}</p>
                      <p className="mt-0.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
                        {c.contact.email && <span className="flex items-center gap-1"><Mail className="h-3.5 w-3.5" aria-hidden />{c.contact.email}</span>}
                        {c.contact.phone && <span className="flex items-center gap-1"><Phone className="h-3.5 w-3.5" aria-hidden />{c.contact.phone}</span>}
                        {!c.contact.email && !c.contact.phone && <span>No contact details</span>}
                        {c.from_document && <Badge tone="info">From a document</Badge>}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="num font-semibold">{money(c.owed_minor, cur)}</p>
                      <p className="text-xs text-muted">owed · {c.open_invoices} open invoice{c.open_invoices === 1 ? "" : "s"}</p>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span className="flex flex-wrap gap-2">
                      {c.overdue_minor > 0 && <Badge tone="warn">{money(c.overdue_minor, cur)} overdue</Badge>}
                      {c.oldest_overdue && <span className="text-xs text-warn">Was due {dateOnly(c.oldest_overdue)} (overdue)</span>}
                      {c.next_due && <span className="text-xs text-muted">Next due {dateOnly(c.next_due)}</span>}
                    </span>
                    <span className="flex gap-1">
                      <Button size="sm" variant="quiet" onClick={() => setEditing(c)}><Pencil className="h-3.5 w-3.5" aria-hidden /> Edit</Button>
                      <Link href="/money-in" className="inline-flex min-h-8 items-center rounded-lg px-2.5 text-sm font-medium text-accent-ink hover:bg-sunken">Invoices</Link>
                    </span>
                  </div>
                </Card>
              </motion.li>
            ))}</ListPresence>
          </ul>
        </>
      )}
      <Modal open={adding === "manual"} onClose={() => setAdding(null)} title="Add a customer">
        {addingShown === "manual" && <CustomerForm onDone={() => setAdding(null)} onCancel={() => setAdding(null)} />}
      </Modal>
      <Modal open={adding === "document"} onClose={() => setAdding(null)} title="Add from a document" wide>
        {addingShown === "document" && <FromDocument onDone={() => setAdding(null)} onCancel={() => setAdding(null)} />}
      </Modal>
      <Modal open={!!editing} onClose={() => setEditing(null)} title={editingShown ? `Edit ${editingShown.name}` : ""}>
        {editingShown && <ContactForm c={editingShown} onDone={() => setEditing(null)} />}
      </Modal>
    </div>
  );
}

function CustomerForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [days, setDays] = useState("7");
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!name.trim()) return setErrs({ name: "Enter the customer's name." });
    setBusy(true);
    setErrs({});
    setErr(null);
    try {
      await apiPost("/api/parties", { kind: "customer", name: name.trim(), email: email || null, phone: phone || null, typical_days_to_pay: Number(days) || 7, accepts_partial: true });
      toast({ tone: "ok", title: "Customer saved" });
      recordsChanged();
      onDone();
    } catch (e2) {
      const f = fieldErrors(e2);
      if (Object.keys(f).length) setErrs(f);
      else setErr(errorText(e2, "Not saved. Try again."));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <Field label="Customer name" htmlFor="c-name" error={errs.name}><Input id="c-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Hotel Rosa" data-autofocus /></Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Email" htmlFor="c-email" optional error={errs.email}><Input id="c-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="accounts@hotelrosa.com" /></Field>
        <Field label="Phone" htmlFor="c-phone" optional error={errs.phone}><Input id="c-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+880 1711 000000" /></Field>
      </div>
      <Field label="Usually pays within (days)" htmlFor="c-days" optional hint="Helps FlowSense estimate when money will arrive."><Input id="c-days" type="number" min={0} max={120} value={days} onChange={(e) => setDays(e.target.value)} /></Field>
      {err && <Notice tone="error">{err}</Notice>}
      <div className="flex justify-end gap-2 border-t border-line pt-4">
        <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button type="submit" busy={busy}>Save customer</Button>
      </div>
    </form>
  );
}

function ContactForm({ c, onDone }: { c: Customer; onDone: () => void }) {
  const [email, setEmail] = useState(c.contact.email ?? "");
  const [phone, setPhone] = useState(c.contact.phone ?? "");
  const [days, setDays] = useState(String(c.typical_days_to_pay));
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErrs({});
    setErr(null);
    try {
      await apiPatch(`/api/parties/${c.id}`, { email: email || null, phone: phone || null, typical_days_to_pay: Number(days) });
      recordsChanged();
      onDone();
    } catch (e2) {
      const f = fieldErrors(e2);
      if (Object.keys(f).length) setErrs(f);
      else setErr(errorText(e2, "Not saved. Try again."));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <Field label="Email" htmlFor="e-email" optional error={errs.email}><Input id="e-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Phone" htmlFor="e-phone" optional error={errs.phone}><Input id="e-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} /></Field>
      <Field label="Usually pays within (days)" htmlFor="e-days"><Input id="e-days" type="number" min={0} max={120} value={days} onChange={(e) => setDays(e.target.value)} /></Field>
      <p className="text-xs text-muted">FlowSense never contacts your customers by itself.</p>
      {err && <Notice tone="error">{err}</Notice>}
      <div className="flex justify-end gap-2 border-t border-line pt-4"><Button type="submit" busy={busy}>Save</Button></div>
    </form>
  );
}

/** Upload -> read -> review every value -> save the customer (and, optionally, the invoice). */
function FromDocument({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const { me } = useSession();
  const toast = useToast();
  const cur = me.business.currency!;
  const [s, setS] = useState<Suggestion | null>(null);
  const [v, setV] = useState<Record<string, string>>({});
  const [withInvoice, setWithInvoice] = useState(true);
  const [busy, setBusy] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  function onRead(sg: Suggestion) {
    const vals: Record<string, string> = {};
    for (const [k, f] of Object.entries(sg.fields)) vals[k] = f.value;
    setV(vals);
    setS(sg);
    // A file already imported as an invoice starts with the invoice unticked (it would be a duplicate).
    setWithInvoice(!!(vals.invoice_number || vals.amount) && !sg.flags.some((f) => f.code === "ALREADY_IMPORTED"));
  }
  const imported = s?.flags.find((f) => f.code === "ALREADY_IMPORTED");
  const set = (k: string) => (e: { target: { value: string } }) => setV((x) => ({ ...x, [k]: e.target.value }));
  const docCurrency = v.currency && v.currency !== cur ? v.currency : null;

  async function save(e: FormEvent) {
    e.preventDefault();
    if (busy || !s) return;
    const f: Record<string, string> = {};
    if (!v.customer_name?.trim()) f.customer_name = "Enter the customer's name.";
    if (withInvoice) {
      if (!v.invoice_number?.trim()) f.invoice_number = "Enter the invoice number.";
      if (!v.amount?.trim()) f.amount = "Enter the amount.";
      if (!v.due_date) f.due_date = "Enter the due date.";
      if (docCurrency) f.amount = `This document is in ${docCurrency}. Enter the amount in ${cur}, or skip the invoice.`;
      if (imported && v.invoice_number?.trim() === s.fields.invoice_number?.value) f.invoice_number = "This file was already imported as this invoice. Change the number only if it really is a different invoice.";
    }
    setErrs(f);
    if (Object.keys(f).length) return;
    setBusy(true);
    setErr(null);
    try {
      const cp = await apiPost<{ id: string }>("/api/parties", { kind: "customer", name: v.customer_name.trim(), email: v.customer_email || null, phone: v.customer_phone || null, accepts_partial: true });
      const docIds = s.documents.map((d) => d.id);
      await apiPost("/api/documents/link", { document_ids: docIds, record_type: "counterparty", record_id: cp.id });
      if (withInvoice) {
        const today = new Date().toISOString().slice(0, 10);
        const inv = await apiPost<{ id: string }>("/api/invoices", {
          customer_id: cp.id, number: v.invoice_number.trim(), description: v.description ?? "", amount: v.amount, currency: cur,
          issued_on: v.issue_date || today, due_on: v.due_date, accepts_partial: true,
        });
        await apiPost("/api/documents/link", { document_ids: docIds, record_type: "invoice", record_id: inv.id });
      }
      toast({ tone: "ok", title: withInvoice ? "Customer and invoice saved" : "Customer saved", text: withInvoice ? "The invoice counts as money owed, not cash, until it's paid." : undefined });
      recordsChanged();
      onDone();
    } catch (e2) {
      const fe = fieldErrors(e2);
      if (e2 instanceof ApiError && e2.code === "INVOICE_DUPLICATE") setErrs({ invoice_number: e2.message });
      else if (Object.keys(fe).length) setErrs(fe);
      else setErr(errorText(e2, "Not saved. Try again."));
    } finally {
      setBusy(false);
    }
  }

  if (!s) return <DocReader purpose="customer" onRead={onRead} hint="An invoice, receipt, order or customer record. Several photos of one invoice are read together." />;
  // The suggestions replace the uploader: they rise in, so the finished read is noticeable.
  return (
    <Reveal>
    <form onSubmit={save} className="space-y-4" noValidate>
      <DocTypeBadge s={s} />
      <SuggestionNotes s={s} />
      <fieldset className="space-y-4">
        <legend className="text-sm font-semibold">Customer</legend>
        <Field label="Customer name" htmlFor="d-name" error={errs.customer_name}><Input id="d-name" value={v.customer_name ?? ""} onChange={set("customer_name")} maxLength={200} /><FieldSource f={s.fields.customer_name} dest="Customer → name" /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Email" htmlFor="d-email" optional error={errs.email}><Input id="d-email" type="email" value={v.customer_email ?? ""} onChange={set("customer_email")} /><FieldSource f={s.fields.customer_email} dest="Customer → email" /></Field>
          <Field label="Phone" htmlFor="d-phone" optional error={errs.phone}><Input id="d-phone" value={v.customer_phone ?? ""} onChange={set("customer_phone")} /><FieldSource f={s.fields.customer_phone} dest="Customer → phone" /></Field>
        </div>
      </fieldset>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" checked={withInvoice} onChange={(e) => setWithInvoice(e.target.checked)} className="mt-1" />
        <span><span className="font-medium">Also add this invoice</span><span className="block text-xs text-muted">Recorded as money the customer owes you. It is not cash until it&apos;s paid.</span></span>
      </label>
      {withInvoice && (
        <fieldset className="space-y-4">
          <legend className="text-sm font-semibold">Invoice</legend>
          {s.missing_for_invoice.length > 0 && <Notice tone="warn">Not found: {s.missing_for_invoice.join(", ")}. Please type {s.missing_for_invoice.length === 1 ? "it" : "them"}.</Notice>}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Invoice number" htmlFor="d-num" error={errs.invoice_number}><Input id="d-num" value={v.invoice_number ?? ""} onChange={set("invoice_number")} maxLength={64} /><FieldSource f={s.fields.invoice_number} dest="Invoice → number" /></Field>
            <Field label={`Amount owed (${cur})`} htmlFor="d-amount" error={errs.amount}><Input id="d-amount" inputMode="decimal" value={v.amount ?? ""} onChange={set("amount")} /><FieldSource f={s.fields.amount} dest="Invoice → amount owed" /></Field>
            <Field label="Invoice date" htmlFor="d-issue" optional><Input id="d-issue" type="date" value={v.issue_date ?? ""} onChange={set("issue_date")} /><FieldSource f={s.fields.issue_date} dest="Invoice → date" /></Field>
            <Field label="Due date" htmlFor="d-due" error={errs.due_date}><Input id="d-due" type="date" value={v.due_date ?? ""} onChange={set("due_date")} /><FieldSource f={s.fields.due_date} dest="Invoice → due date" /></Field>
          </div>
          <Field label="What it was for" htmlFor="d-desc" optional><Input id="d-desc" value={v.description ?? ""} onChange={set("description")} maxLength={300} /><FieldSource f={s.fields.description} dest="Invoice → description" /></Field>
          {(s.fields.items || s.fields.subtotal || s.fields.tax || s.fields.currency) && (
            // Read for checking: an invoice keeps one total in the books' currency, so these aren't stored separately.
            <div className="rounded-xl bg-sunken p-3 text-sm" data-testid="doc-reference">
              <p className="text-xs font-medium text-muted">Also on the document (for checking, not saved separately)</p>
              <dl className="mt-1.5 space-y-1">
                {(["items", "subtotal", "tax", "currency"] as const).map((k) => s.fields[k] && (
                  <div key={k} className="grid gap-x-3 sm:grid-cols-[8rem_1fr]">
                    <dt className="text-muted">{s.fields[k].label}</dt>
                    <dd className="break-words"><span className="num">{s.fields[k].value}</span><FieldSource f={{ ...s.fields[k], note: undefined }} /></dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </fieldset>
      )}
      {err && <Notice tone="error">{err}</Notice>}
      <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-4">
        <Button variant="quiet" onClick={() => setS(null)} disabled={busy}>Read a different file</Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button type="submit" busy={busy}>{withInvoice ? "Save customer and invoice" : "Save customer"}</Button>
      </div>
    </form>
    </Reveal>
  );
}
