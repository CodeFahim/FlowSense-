"use client";

import { ListPresence, itemMotion, motion } from "@/components/motion";
import clsx from "clsx";
import { AlertTriangle, ChevronDown, FileCheck2, FileText, FileUp, Pencil, Plus, ReceiptText, Search, ShieldCheck, UploadCloud, UserRound } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { recordsChanged, useRecordsChanged } from "@/components/records";
import { MoneyTabs } from "@/components/money-tabs";
import { useSession } from "@/components/shell";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, Empty, Field, Input, Loading, Notice, Segmented, Spinner, Status, Textarea } from "@/components/ui";
import { ApiError, apiGet, apiPatch, apiPost, apiUpload } from "@/lib/api";
import { dateOnly, money } from "@/lib/format";
import { errorText } from "@/lib/outcomes";
import type { Evidence, Invoice } from "@/lib/types";

type Filter = "all" | "open" | "overdue" | "paid" | "disputed";

function todayIn(sandboxNow: string | null, tz: string | null) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz ?? "UTC", year: "numeric", month: "2-digit", day: "2-digit" }).format(sandboxNow ? new Date(sandboxNow) : new Date());
}

export default function MoneyInPage() {
  const { me, openAdd } = useSession();
  const params = useSearchParams();
  const [list, setList] = useState<Invoice[] | null>(null);
  const [sel, setSel] = useState<Invoice | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const detailRef = useRef<HTMLDivElement>(null);
  const today = todayIn(me.business.sandbox_now, me.business.timezone);
  const wanted = params.get("invoice");

  const open = useCallback(async (id: string, scroll = false) => {
    try {
      setSel(await apiGet<Invoice>(`/api/invoices/${id}`));
      if (scroll && window.innerWidth < 1024) detailRef.current?.scrollIntoView({ block: "start" });
    } catch (e) {
      setErr(errorText(e, "The invoice could not be loaded."));
    }
  }, []);

  const load = useCallback(() => {
    apiGet<Invoice[]>("/api/invoices").then(
      (l) => {
        setList(l);
        const target = (wanted && l.find((i) => i.id === wanted)) || (sel && l.find((i) => i.id === sel.id)) || l[0];
        if (target) void open(target.id, !!wanted);
        else setSel(null);
      },
      (e) => setErr(errorText(e, "Invoices could not be loaded.")),
    );
    // sel is read for re-selection only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, wanted]);
  useEffect(load, [load]);
  useRecordsChanged(load);

  const state = useCallback((i: Invoice) => (i.disputed ? "disputed" : i.outstanding_minor === 0 ? "paid" : i.due_on < today ? "overdue" : "open"), [today]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (list ?? []).filter((i) => {
      const s = state(i);
      const fit = filter === "all" || filter === s || (filter === "open" && s === "overdue");
      return fit && (!needle || `${i.number} ${i.customer?.name ?? ""} ${i.description}`.toLowerCase().includes(needle));
    });
  }, [list, q, filter, state]);

  if (err) return <Notice tone="error" title="Could not load Money in">{err}</Notice>;
  if (!list) return <Loading what="Loading invoices" />;
  const cur = me.business.currency ?? undefined;
  const totalOpen = list.reduce((s, i) => s + i.outstanding_minor, 0);

  return (
    <div className="space-y-6">
      <MoneyTabs />
      <p className="text-sm text-muted">Money customers owe you, and the evidence behind it. Invoices are not cash: they count only when the payment arrives.</p>
      {list.length === 0 ? (
        <Card>
          <Empty icon={ReceiptText} title="No invoices yet" action={<Button onClick={() => openAdd("invoice")}><Plus className="h-4 w-4" aria-hidden /> Add your first invoice</Button>}>
            Add invoices customers haven&apos;t paid yet, with the amount and due date. {me.business.kind === "workspace" && <>If nobody owes you money, mark that in the <Link href="/setup" className="font-medium text-accent-ink underline">setup checklist</Link>.</>}
          </Empty>
        </Card>
      ) : (
        <>
          <p className="text-sm text-muted">Outstanding: <span className="num font-semibold text-ink">{money(totalOpen, cur)}</span> across {list.filter((i) => i.outstanding_minor > 0).length} invoice(s).</p>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
            <div className="min-w-0 space-y-3">
              <div className="relative">
                <Search className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
                <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search number or customer" aria-label="Search invoices" className="pl-9" />
              </div>
              <div className="overflow-x-auto pb-1">
                <Segmented<Filter> label="Filter invoices" nowrap value={filter} onChange={setFilter} options={[{ value: "all", label: "All" }, { value: "open", label: "Open" }, { value: "overdue", label: "Overdue" }, { value: "paid", label: "Paid" }, { value: "disputed", label: "Disputed" }]} />
              </div>
              {shown.length === 0 ? (
                <Card><Empty icon={FileText} title="No invoices match">Try another search or filter.</Empty></Card>
              ) : (
                <ul className="space-y-2" aria-label="Invoices">
                  <ListPresence>{shown.map((i) => (
                    <motion.li {...itemMotion} key={i.id}>
                      <button type="button" onClick={() => open(i.id, true)} aria-current={sel?.id === i.id ? "true" : undefined} className={clsx("w-full rounded-xl border px-4 py-3 text-left", sel?.id === i.id ? "border-accent bg-surface ring-1 ring-accent" : "border-line bg-surface hover:border-line-strong")}>
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-semibold">{i.number}</span>
                          <span className="num text-sm font-semibold">{money(i.outstanding_minor, cur)}</span>
                        </div>
                        <div className="mt-1 flex items-center justify-between gap-2 text-xs text-muted">
                          <span className="truncate">{i.customer?.name} · due {dateOnly(i.due_on)}</span>
                          <InvoiceState s={state(i)} />
                        </div>
                      </button>
                    </motion.li>
                  ))}</ListPresence>
                </ul>
              )}
            </div>
            <div ref={detailRef} className="min-w-0 scroll-mt-20 space-y-6">
              {sel ? <InvoiceDetail inv={sel} overdue={state(sel) === "overdue"} today={today} onChanged={() => { recordsChanged(); return open(sel.id); }} /> : <Loading />}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function InvoiceState({ s }: { s: string }) {
  if (s === "overdue") return <Badge tone="warn" icon={AlertTriangle}>Overdue, not received</Badge>;
  if (s === "paid") return <Badge tone="ok">Paid</Badge>;
  if (s === "disputed") return <Badge tone="danger">Disputed</Badge>;
  return <Badge>Not yet due</Badge>;
}

function InvoiceDetail({ inv, overdue, today, onChanged }: { inv: Invoice; overdue: boolean; today: string; onChanged: () => Promise<void> }) {
  const { me } = useSession();
  const cur = me.business.currency ?? undefined;
  const [editing, setEditing] = useState(false);
  const paidPct = inv.amount_minor ? Math.round((inv.collected_minor / inv.amount_minor) * 100) : 0;
  const daysLate = overdue ? Math.round((Date.parse(today) - Date.parse(inv.due_on)) / 86_400_000) : 0;
  return (
    <>
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold">{inv.number}</h2>
            <p className="text-sm text-muted">{inv.customer?.name}. {inv.description}</p>
          </div>
          <div className="flex items-center gap-2">
            <InvoiceState s={inv.disputed ? "disputed" : inv.outstanding_minor === 0 ? "paid" : overdue ? "overdue" : "open"} />
            <Button size="sm" variant="quiet" onClick={() => setEditing((e) => !e)} aria-expanded={editing}><Pencil className="h-3.5 w-3.5" aria-hidden /> Edit</Button>
          </div>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-3">
          <div><p className="text-xs text-muted">Still owed</p><p className="num text-xl font-semibold">{money(inv.outstanding_minor, cur)}</p></div>
          <div><p className="text-xs text-muted">Received (settled)</p><p className="num text-xl font-semibold text-ok">{money(inv.collected_minor, cur)}</p></div>
          <div><p className="text-xs text-muted">Invoice total</p><p className="num text-xl">{money(inv.amount_minor, cur)}</p></div>
        </div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-valuenow={paidPct} aria-valuemin={0} aria-valuemax={100} aria-label="Share received">
          <div className="bar-fill h-full bg-ok" style={{ width: `${paidPct}%` }} />
        </div>
        <p className="mt-3 text-sm text-muted">
          Issued {dateOnly(inv.issued_on)}. Due {dateOnly(inv.due_on)}
          {overdue && <span className="font-medium text-warn"> · {daysLate} day{daysLate === 1 ? "" : "s"} overdue and not received. It is not counted as cash.</span>}
        </p>
        {editing && <EditInvoice inv={inv} onDone={async () => { setEditing(false); await onChanged(); }} />}
        {inv.requests && inv.requests.length > 0 && (
          <div className="mt-4 border-t border-line pt-4">
            <h3 className="mb-2 text-sm font-medium">Payment requests for this invoice</h3>
            <ul className="space-y-2 text-sm">
              {inv.requests.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-2">
                  <span className="num font-medium">{money(r.amount_minor, cur)}</span> <Status value={r.status} />
                  {r.settled_minor > 0 && <span className="text-xs text-muted">{money(r.settled_minor, cur)} received</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {inv.outstanding_minor > 0 && <RecordPayment inv={inv} onDone={onChanged} />}
      </Card>
      <Card id="evidence" title="Evidence" description="Documents that support the invoice. Their text is stored as untrusted data and never followed as instructions. Saving a document does not verify it.">
        {inv.evidence.length === 0 ? (
          <Empty icon={FileText} title="No evidence yet">Upload a PDF or type a note below.</Empty>
        ) : (
          <ul className="space-y-3">{inv.evidence.map((e) => <EvidenceItem key={e.id} e={e} />)}</ul>
        )}
        <div className="mt-5 space-y-4 border-t border-line pt-5">
          <PdfUpload invoiceId={inv.id} onAdded={onChanged} />
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 text-sm font-medium text-muted hover:text-ink">
              <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" aria-hidden /> Or type a note as evidence
            </summary>
            <AddTextEvidence invoiceId={inv.id} onAdded={onChanged} />
          </details>
        </div>
      </Card>
    </>
  );
}

function EditInvoice({ inv, onDone }: { inv: Invoice; onDone: () => Promise<void> }) {
  const { me } = useSession();
  const cur = me.business.currency!;
  const toast = useToast();
  const locked = inv.collected_minor > 0 || (inv.requests ?? []).some((r) => ["proposed", "approved", "sent", "accepted", "partially_settled"].includes(r.status));
  const [due, setDue] = useState(inv.due_on);
  const [desc, setDesc] = useState(inv.description);
  const [amount, setAmount] = useState("");
  const [disputed, setDisputed] = useState(inv.disputed);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await apiPatch(`/api/invoices/${inv.id}`, { due_on: due, description: desc, amount: amount || null, currency: cur, disputed });
      toast({ tone: "ok", title: `${inv.number} updated`, text: "The change is in the activity log; the plan was refreshed." });
      await onDone();
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : "Not saved. Try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="mt-4 space-y-4 rounded-xl border border-line bg-sunken p-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Due date" htmlFor="ed-due"><Input id="ed-due" type="date" value={due} min={inv.issued_on} onChange={(e) => setDue(e.target.value)} /></Field>
        <Field label={`New amount (${cur})`} htmlFor="ed-amount" optional hint={locked ? "Locked: money was received or requested. Record a correction instead." : `Currently ${money(inv.amount_minor, cur)}.`}>
          <Input id="ed-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} disabled={locked} placeholder="Leave empty to keep" className="num" />
        </Field>
      </div>
      <Field label="Description" htmlFor="ed-desc"><Input id="ed-desc" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={500} /></Field>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={disputed} onChange={(e) => setDisputed(e.target.checked)} /> The customer disputes this invoice (it won&apos;t be used in plans)</label>
      {err && <Notice tone="error">{err}</Notice>}
      <div className="flex justify-end"><Button type="submit" size="sm" busy={busy}>Save changes</Button></div>
    </form>
  );
}

function RecordPayment({ inv, onDone }: { inv: Invoice; onDone: () => Promise<void> }) {
  const { me } = useSession();
  const toast = useToast();
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (me.business.kind !== "workspace") {
    return <p className="mt-4 border-t border-line pt-4 text-xs text-muted">In this sample, customer payments come from the sandbox simulator on the Approvals page.</p>;
  }
  if (me.role !== "owner") return null;
  const cur = me.business.currency!;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await apiPost<{ detail: string }>(`/api/invoices/${inv.id}/payments`, { amount, currency: cur });
      toast({ tone: "ok", title: "Payment recorded", text: `${r.detail} Recorded by you, not bank-verified.` });
      setAmount("");
      await onDone();
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : "Not saved. Try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="mt-4 space-y-3 border-t border-line pt-4">
      <h3 className="text-sm font-medium">Record money received</h3>
      <div className="flex flex-wrap items-end gap-2">
        <Field label={`Amount received (${cur})`} htmlFor={`pay-${inv.id}`}>
          <Input id={`pay-${inv.id}`} inputMode="decimal" required value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={`up to ${money(inv.outstanding_minor, cur)}`} className="num" />
        </Field>
        <Button type="submit" variant="secondary" busy={busy}>Record payment</Button>
      </div>
      <p className="text-xs text-muted">Use this only for money that has actually arrived. It increases your available cash.</p>
      {err && <Notice tone="error">{err}</Notice>}
    </form>
  );
}

const PROV = {
  sandbox_verified: { text: "Verified record", tone: "ok" as const, icon: ShieldCheck, note: "Matched against a sandbox registry." },
  user_supplied: { text: "Uploaded, not verified", tone: "neutral" as const, icon: UserRound, note: "Supplied by a user. Not checked against any record." },
  ai_assessment: { text: "AI interpretation", tone: "info" as const, icon: FileCheck2, note: "Generated, not a source document." },
};

function EvidenceItem({ e }: { e: Evidence }) {
  const p = PROV[e.provenance];
  return (
    <li className="rounded-xl border border-line p-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">{e.title}</p>
          <p className="mt-0.5 text-xs break-words text-muted">{e.label}{e.kind === "pdf" ? ". Text extracted from a PDF (the file itself is not kept)" : ". Typed note"}</p>
        </div>
        <Badge tone={p.tone} icon={p.icon} title={p.note}>{p.text}</Badge>
      </div>
      {e.instruction_like && (
        <div className="mt-3 flex gap-2 rounded-lg bg-danger-soft px-3 py-2 text-xs" role="note">
          <AlertTriangle className="h-4 w-4 shrink-0 text-danger" aria-hidden />
          <span>Contains text that looks like instructions. FlowSense treats it only as data and never follows it.</span>
        </div>
      )}
      <details className="mt-3">
        <summary className="cursor-pointer text-xs font-medium text-muted hover:text-ink">{e.kind === "pdf" ? "Show extracted text" : "Show text"} ({e.chars.toLocaleString()} characters)</summary>
        {/* Plain text only: React escapes it; no HTML or Markdown is rendered. */}
        <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-sunken p-3 font-sans text-xs whitespace-pre-wrap break-words">{e.content_text}</pre>
        <p className="mt-1 text-[0.7rem] break-all text-faint">SHA-256 {e.sha256}</p>
      </details>
    </li>
  );
}

function AddTextEvidence({ invoiceId, onAdded }: { invoiceId: string; onAdded: () => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  async function submit(ev: FormEvent) {
    ev.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const e = await apiPost<Evidence>(`/api/invoices/${invoiceId}/evidence`, { title, text });
      setMsg({ tone: "ok", text: `Saved as ${e.label}, marked as your note (not verified)${e.instruction_like ? "; instruction-like text was flagged" : ""}.` });
      setTitle("");
      setText("");
      await onAdded();
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e, "The note was not saved.") });
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="mt-3 space-y-3">
      <Field label="Title" htmlFor={`ev-title-${invoiceId}`}>
        <Input id={`ev-title-${invoiceId}`} value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} placeholder="e.g. Customer email confirming delivery" />
      </Field>
      <Field label="Text" htmlFor={`ev-text-${invoiceId}`} hint="Plain text, up to 20,000 characters.">
        <Textarea id={`ev-text-${invoiceId}`} value={text} onChange={(e) => setText(e.target.value)} required maxLength={20000} rows={4} />
      </Field>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <Button type="submit" variant="secondary" busy={busy}>Save note</Button>
    </form>
  );
}

const MAX_PDF_BYTES = 5 * 1024 * 1024;

const PDF_HINTS: Record<string, string> = {
  PDF_NO_TEXT: "This looks like a scanned or image-only PDF, and invoice evidence keeps only a PDF's own text. Type the key facts as a note, or use “Add from a document” on Customers, which can read scans.",
  PDF_ENCRYPTED: "Remove the password (for example, print it to a new PDF) and upload that copy.",
  PDF_TOO_MANY_PAGES: "Upload only the pages that matter (20 pages at most).",
  PDF_TOO_LARGE: "Make the file smaller than 5 MB, for example by keeping only the relevant pages.",
  BODY_TOO_LARGE: "Make the file smaller than 5 MB, for example by keeping only the relevant pages.",
  PDF_NOT_PDF: "The file's contents are not a PDF, whatever its name says. Export the document as PDF and try again.",
  PDF_EXTENSION: "Choose a file ending in .pdf.",
  PDF_MALFORMED: "The file is damaged or uses features FlowSense cannot read. Re-export it as a standard PDF.",
  PDF_TIMEOUT: "The file took too long to read. Re-export it as a simpler PDF.",
  PDF_DUPLICATE: "Nothing new was stored; the existing evidence is listed above.",
};

type Preview = { pages: number; truncated: boolean; chars: number; instruction_like: boolean; content_text: string };
type PdfResult = Evidence & { pages: number; truncated: boolean };
type PdfMsg = { tone: "ok" | "error" | "info"; title: string; text?: string };

/** Upload → review the extracted text → save. Nothing is stored until the user presses Save. */
function PdfUpload({ invoiceId, onAdded }: { invoiceId: string; onAdded: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState<"preview" | "save" | null>(null);
  const [drag, setDrag] = useState(false);
  const [sent, setSent] = useState<number | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [msg, setMsg] = useState<PdfMsg | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const inputId = `pdf-file-${invoiceId}`;

  function pick(f: File | null) {
    setMsg(null);
    setPreview(null);
    if (f && f.size > MAX_PDF_BYTES) {
      setFile(null);
      setMsg({ tone: "error", title: "This PDF is larger than 5 MB", text: PDF_HINTS.PDF_TOO_LARGE });
      return;
    }
    setFile(f);
  }
  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    if (!busy) pick(e.dataTransfer.files?.[0] ?? null);
  }
  function reject(e: unknown) {
    if (e instanceof ApiError) {
      const duplicate = e.code === "PDF_DUPLICATE";
      setMsg({ tone: duplicate ? "info" : "error", title: duplicate ? e.message : `Not stored: ${e.message}`, text: PDF_HINTS[e.code] });
    } else {
      setMsg({ tone: "error", title: "Upload did not complete", text: errorText(e, "") + " Refresh the page: if the evidence is listed, it was stored." });
    }
  }
  async function send(isPreview: boolean) {
    if (!file || busy) return;
    setBusy(isPreview ? "preview" : "save");
    setSent(0);
    setMsg(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (title.trim()) form.append("title", title.trim());
      if (isPreview) {
        form.append("preview", "true");
        setPreview(await apiUpload<Preview>(`/api/invoices/${invoiceId}/evidence/pdf`, form, setSent));
      } else {
        const e = await apiUpload<PdfResult>(`/api/invoices/${invoiceId}/evidence/pdf`, form, setSent);
        setMsg({ tone: "ok", title: `Saved as ${e.label}`, text: `Text from ${e.pages} page${e.pages === 1 ? "" : "s"} attached as your upload (not verified). The PDF file itself was not kept.${e.instruction_like ? " Instruction-like text is flagged and never followed." : ""}` });
        setFile(null);
        setTitle("");
        setPreview(null);
        setInputKey((k) => k + 1);
        await onAdded();
      }
    } catch (e) {
      reject(e);
    } finally {
      setBusy(null);
      setSent(null);
    }
  }
  // Real upload progress while the file is sent; after that the server is reading it.
  const uploading = sent !== null && sent < 1;
  return (
    <form onSubmit={(e) => { e.preventDefault(); void send(!preview); }} className="space-y-3" aria-busy={!!busy}>
      <h3 className="text-sm font-medium">Upload a PDF</h3>
      <label
        htmlFor={inputId}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        data-over={drag}
        className={clsx("dropzone flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-6 text-center text-sm", drag ? "border-accent bg-accent-soft" : "border-line-strong hover:bg-sunken", busy && "pointer-events-none opacity-60")}
      >
        {file ? <FileUp className="h-6 w-6 text-accent" aria-hidden /> : <UploadCloud className="h-6 w-6 text-muted" aria-hidden />}
        <span className="mt-2 font-medium break-all">{file ? file.name : "Drop a PDF here, or click to choose a file"}</span>
        <span className="mt-1 text-xs text-muted">PDF only, up to 5 MB and 20 pages. Only the text is kept. Evidence keeps only a PDF&apos;s own text: for a scan, type a note or use “Add from a document” on Customers, which reads scans.</span>
      </label>
      <input id={inputId} key={inputKey} type="file" accept="application/pdf,.pdf" disabled={!!busy} onChange={(e) => pick(e.target.files?.[0] ?? null)} className="sr-only" aria-label="PDF file" />
      <Field label="Title" htmlFor={`pdf-title-${invoiceId}`} optional>
        <Input id={`pdf-title-${invoiceId}`} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="e.g. Signed remittance advice" disabled={!!busy} />
      </Field>
      {preview && (
        <div className="space-y-2 rounded-xl border border-line bg-sunken p-3">
          <p className="text-sm font-medium">Review the extracted text before saving</p>
          <p className="text-xs text-muted">{preview.pages} page{preview.pages === 1 ? "" : "s"}, {preview.chars.toLocaleString()} characters{preview.truncated ? " (cut at 20,000)" : ""}. Not stored yet.</p>
          {preview.instruction_like && <Notice tone="warn">This text contains instruction-like wording. It will be flagged and never followed.</Notice>}
          <pre className="max-h-48 overflow-auto rounded-lg bg-surface p-3 font-sans text-xs whitespace-pre-wrap break-words">{preview.content_text}</pre>
        </div>
      )}
      <div aria-live="polite">
        {busy && <Spinner label={uploading ? `Uploading the PDF… ${Math.round((sent ?? 0) * 100)}%` : busy === "preview" ? "Reading the PDF…" : "Saving…"} />}
        {msg && <Notice tone={msg.tone} title={msg.title}>{msg.text && <p>{msg.text}</p>}</Notice>}
      </div>
      <div className="flex flex-wrap gap-2">
        {preview ? (
          <>
            <Button type="submit" busy={busy === "save"}>Save as evidence</Button>
            <Button variant="secondary" onClick={() => pick(null)} disabled={!!busy}>Discard</Button>
          </>
        ) : (
          <Button type="submit" busy={busy === "preview"} disabled={!file}>Read PDF</Button>
        )}
      </div>
    </form>
  );
}
