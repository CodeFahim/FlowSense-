"use client";

import { ListPresence, itemMotion, motion } from "@/components/motion";
import clsx from "clsx";
import { Banknote, CheckCircle2, FileText, Inbox, RefreshCw, Send, ShieldCheck, Users, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApprovalDialog } from "@/components/approval-dialog";
import { useSession } from "@/components/shell";
import { Badge, Button, Card, Empty, Field, Input, Loading, Modal, Notice, PageHeader, ReasonList, Status, Tabs } from "@/components/ui";
import { recordsChanged } from "@/components/records";
import { apiGet, apiPost, newIdempotencyKey } from "@/lib/api";
import { dateTime, digitsFor, label, money } from "@/lib/format";
import { describeError, describeEvent, errorText, isDefinitive, type Notice as Outcome } from "@/lib/outcomes";
import type { Action, Counterparty, Policy } from "@/lib/types";

type Flash = Outcome | null;
type Group = "approve" | "ready" | "progress" | "done";

const GROUPS: { value: Group; label: string; statuses: string[]; empty: string }[] = [
  { value: "approve", label: "Needs approval", statuses: ["proposed"], empty: "Nothing is waiting for your approval." },
  { value: "ready", label: "Ready to send", statuses: ["approved"], empty: "Nothing approved is waiting to be sent." },
  { value: "progress", label: "In progress", statuses: ["sent", "accepted", "submitted"], empty: "Nothing is waiting on a customer or the bank." },
  { value: "done", label: "Done", statuses: ["settled", "partially_settled", "refused", "cancelled", "denied", "failed"], empty: "No finished actions yet." },
];

// What each state means for the user, in plain words.
const MEANING: Record<string, string> = {
  submitted: "Sent to the sandbox bank. The money is set aside until the bank confirms. If confirmation does not arrive, check the bank status.",
  sent: "Sent to the customer (simulated). No money has arrived yet.",
  accepted: "The customer promised to pay. A promise is not cash, so your balance changes only when the payment settles.",
  refused: "The customer declined. No money moved, and the rescue plan was recalculated without this customer.",
  partially_settled: "The customer paid less than requested. The amount received counts; the rest triggered a new plan.",
  denied: "Refused by the safety rules when it was proposed, so it can never be approved or paid.",
  failed: "The bank reported a failure. No money left the account and the set-aside amount was released.",
};

function title(a: Action): string {
  const who = a.counterparty?.name ?? "recipient";
  if (a.type === "payment_request") return `Payment request to ${who}`;
  if (a.type === "payroll_payment") return `Payroll payment to ${who}`;
  return `${label(a.type)} to ${who}`;
}

export default function ApprovalsPage() {
  const { me } = useSession();
  const [actions, setActions] = useState<Action[] | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Group | null>(null);

  // After acting on an item, show the tab it moved to, so it never silently disappears.
  const load = useCallback(async (follow?: string) => {
    try {
      const list = await apiGet<Action[]>("/api/actions");
      setActions(list);
      const moved = follow && list.find((a) => a.id === follow);
      if (moved) setTab(GROUPS.find((g) => g.statuses.includes(moved.status))?.value ?? null);
      setErr(null);
    } catch (e) {
      setErr(errorText(e, "Approvals could not be loaded."));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const counts = useMemo(() => Object.fromEntries(GROUPS.map((g) => [g.value, (actions ?? []).filter((a) => g.statuses.includes(a.status)).length])) as Record<Group, number>, [actions]);
  const active: Group = tab ?? GROUPS.find((g) => counts[g.value] > 0)?.value ?? "approve";

  if (err) return <Notice tone="error" title="Could not load approvals">{err}</Notice>;
  if (!actions) return <Loading what="Loading approvals" />;
  const group = GROUPS.find((g) => g.value === active)!;
  const list = actions.filter((a) => group.statuses.includes(a.status));

  return (
    <div className="space-y-6">
      <PageHeader title="Approvals" description="Nothing moves until the owner approves it. Approving and sending are separate steps, and the safety rules check everything again at each one." />
      {flash && (
        <Notice tone={flash.tone} title={flash.title} action={<button type="button" aria-label="Dismiss" onClick={() => setFlash(null)} className="h-6 w-6 rounded text-muted hover:text-ink"><X className="mx-auto h-4 w-4" /></button>}>
          {flash.text && <p>{flash.text}</p>}
          {flash.reasons && <ReasonList reasons={flash.reasons} />}
        </Notice>
      )}
      {me.role !== "owner" && <Notice tone="info">You are signed in as staff. You can review actions and request recipient changes; only the owner can approve or send.</Notice>}

      <div>
        <Tabs<Group> label="Approval queue" value={active} onChange={setTab} tabs={GROUPS.map((g) => ({ value: g.value, label: g.label, count: counts[g.value] }))} />
        <div className="pt-4">
          {list.length === 0 ? (
            <Card><Empty icon={Inbox} title={group.empty}>{active === "approve" ? "The rescue plan drafts actions when a shortfall appears." : undefined}</Empty></Card>
          ) : (
            <ul className="space-y-3">{list.map((a) => <ActionItem key={a.id} a={a} onChanged={load} setFlash={setFlash} />)}</ul>
          )}
        </div>
      </div>

      <Recipients onChanged={load} setFlash={setFlash} />
      <Policies setFlash={setFlash} />
    </div>
  );
}

function ActionItem({ a, onChanged, setFlash }: { a: Action; onChanged: (follow?: string) => Promise<void>; setFlash: (f: Flash) => void }) {
  const { me, reload, refreshCounts } = useSession();
  const owner = me.role === "owner";
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [payOpen, setPayOpen] = useState(false);
  const [payAmount, setPayAmount] = useState("");
  // One idempotency key per submission intent: a double click or retry reuses it.
  const keyRef = useRef<string | null>(null);
  const stale = a.counterparty && a.counterparty.version !== a.counterparty_version;
  const isReq = a.type === "payment_request";
  const cur = a.currency;

  // describeEvent never reports success for "no change" or pending outcomes.
  const inFlight = useRef(false);
  async function run(fn: () => Promise<unknown>, ok: string, onError?: (e: unknown) => void) {
    // One action at a time per item: a fast double click can't start a second request.
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    let flash: Flash;
    try {
      flash = describeEvent(((await fn()) ?? null) as { outcome?: string; detail?: string } | null, ok);
    } catch (e) {
      onError?.(e);
      flash = describeError(e);
    }
    // Refresh first, then show the outcome: the message appears only once the screen is up to date, so a
    // tab chosen after reading it is never overridden by a late "follow the item" refresh.
    try {
      await Promise.all([onChanged(a.id), reload()]);
      refreshCounts();
      // Balances change only when the backend records a settlement; every screen re-reads them from there.
      recordsChanged();
    } finally {
      inFlight.current = false;
      setBusy(false);
      setFlash(flash);
    }
  }

  function submit() {
    keyRef.current ??= newIdempotencyKey();
    const key = keyRef.current;
    return run(
      async () => {
        const r = await apiPost<{ status: string; settlement?: { outcome?: string; detail?: string } | null }>(`/api/actions/${a.id}/execute`, {}, { "Idempotency-Key": key });
        keyRef.current = null;
        if (r.settlement) return r.settlement;
        return { outcome: r.status, detail: r.status === "sent" ? "The request went to the sandbox customer (simulated; no real message)." : `Status: ${r.status}` };
      },
      isReq ? "Request sent (simulated)" : "Paid — confirmed by the sandbox bank",
      (e) => {
        // A definitive answer (e.g. kill switch): the next attempt must be a new one.
        if (isDefinitive(e)) keyRef.current = null;
      },
    );
  }

  const check = a.status === "proposed" ? a.current_check : null;
  const remaining = a.amount_minor - a.settled_minor;
  const Icon = isReq ? FileText : a.type === "payroll_payment" ? Users : Banknote;

  return (
    <li className="rounded-xl border border-line bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 gap-3">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-sunken text-muted" aria-hidden><Icon className="h-[1.1rem] w-[1.1rem]" /></span>
          <div className="min-w-0">
            <h3 className="font-semibold">{title(a)}</h3>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <Status value={a.status} />
              {a.source === "planner" && <Badge>From the rescue plan</Badge>}
              {a.source === "assistant" && <Badge>Drafted by the assistant</Badge>}
              {stale && <Badge tone="danger">Recipient changed</Badge>}
            </div>
          </div>
        </div>
        <div className="num text-right">
          <p className="text-lg font-semibold tracking-[-0.01em]">{money(a.amount_minor, cur)}</p>
          {isReq && a.settled_minor > 0 && <p className="text-xs text-muted">{money(a.settled_minor, cur)} received</p>}
        </div>
      </div>

      <dl className="mt-4 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        <div className="min-w-0"><dt className="text-xs text-muted">Reason</dt><dd className="break-words">{a.purpose}</dd></div>
        <div className="min-w-0">
          <dt className="text-xs text-muted">{isReq ? "Customer" : "Recipient"}</dt>
          <dd className="break-words">
            {a.counterparty?.name}{" "}
            {stale ? (
              <span className="text-muted">bound to v{a.counterparty_version}; current details v{a.counterparty?.version}: <code>{a.counterparty?.account_ref}</code></span>
            ) : (
              <span className="text-muted"><code>{a.counterparty?.account_ref}</code> v{a.counterparty_version}</span>
            )}
            {a.counterparty && !a.counterparty.verified && <span className="font-medium text-danger"> (unverified)</span>}
          </dd>
        </div>
        {(a.invoice || a.obligation) && (
          <div className="min-w-0">
            <dt className="text-xs text-muted">{a.invoice ? "Invoice" : "Due"}</dt>
            <dd>{a.invoice ? `${a.invoice.number}, ${money(a.invoice.outstanding_minor, cur)} outstanding` : dateTime(a.obligation!.due_at, me.business.timezone ?? undefined)}</dd>
          </div>
        )}
        {a.approval && (
          <div className="min-w-0">
            <dt className="text-xs text-muted">Approval</dt>
            <dd>
              {a.approval.revoked_at ? <span className="text-danger">Revoked: {a.approval.revoke_reason}</span> : a.approval.consumed_at ? "Used" : `Valid until ${dateTime(a.approval.expires_at)} (single use)`}
            </dd>
          </div>
        )}
        {a.transfer && <div className="min-w-0"><dt className="text-xs text-muted">Bank reference</dt><dd><code className="break-all">{a.transfer.provider_ref}</code></dd></div>}
      </dl>

      {MEANING[a.status] && <p className="mt-3 rounded-lg bg-sunken px-3 py-2 text-sm" role="note">{MEANING[a.status]}</p>}
      {a.status !== "proposed" && a.last_decision?.outcome === "DENY" && <div className="mt-3"><ReasonList reasons={a.last_decision.reasons} /></div>}
      {check && (check.outcome === "DENY" || check.warnings.length > 0) && (
        <div className="mt-3 rounded-lg bg-warn-soft px-3 py-2.5">
          <p className="mb-1.5 text-sm font-medium text-warn">Would be blocked right now</p>
          <ReasonList reasons={[...check.reasons, ...check.warnings]} tone="warn" />
          <p className="mt-1.5 text-xs text-muted">Based on current balances. The rules run again when you approve and when you send.</p>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {a.status === "proposed" && owner && <Button onClick={() => setOpen(true)}><ShieldCheck className="h-4 w-4" aria-hidden /> Review &amp; approve</Button>}
        {a.status === "approved" && owner && (
          <Button onClick={submit} busy={busy}><Send className="h-4 w-4" aria-hidden /> {isReq ? "Send request (simulated)" : "Submit to sandbox bank"}</Button>
        )}
        {a.status === "submitted" && (
          <Button
            variant="secondary"
            busy={busy}
            onClick={() =>
              run(async () => {
                const r = await apiPost<{ checked: number; results: { outcome?: string; detail?: string }[] }>("/api/reconcile");
                if (r.checked === 0) return { outcome: "duplicate", detail: "No transfers were waiting for the bank." };
                const failed = r.results.find((x) => x.outcome === "failed");
                return { outcome: failed ? "failed" : r.results[0]?.outcome, detail: r.results.map((x) => x.detail).join(" ") };
              }, "Bank confirmed — reconciliation complete")
            }
          >
            <RefreshCw className="h-4 w-4" aria-hidden /> Check bank status
          </Button>
        )}
        {stale && ["proposed", "approved"].includes(a.status) && (
          <Button variant="secondary" busy={busy} onClick={() => run(() => apiPost(`/api/actions/${a.id}/revise`, { rebind_recipient: true }), "Moved to the new recipient details; a new approval is required")}>
            Rebind to recipient v{a.counterparty?.version}
          </Button>
        )}
        {["proposed", "approved"].includes(a.status) && owner && (
          <Button variant="quiet" busy={busy} onClick={() => run(() => apiPost(`/api/actions/${a.id}/cancel`, { reason: "Cancelled from Approvals" }), "Action cancelled")}>Cancel action</Button>
        )}
      </div>

      {isReq && ["sent", "accepted"].includes(a.status) && owner && me.demo_controls && (
        <div className="mt-4 rounded-xl border border-dashed border-line-strong p-3">
          <p className="text-xs font-medium text-muted">Sandbox simulator: play the customer (creates signed test events, no real money)</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {a.status === "sent" && (
              <Button size="sm" variant="secondary" busy={busy} onClick={() => run(() => apiPost("/api/sandbox/simulate/customer-accept", { action_id: a.id }), "Customer accepted (no cash yet)")}>Customer accepts</Button>
            )}
            {a.settled_minor === 0 && (
              <Button size="sm" variant="secondary" busy={busy} onClick={() => run(() => apiPost("/api/sandbox/simulate/customer-refuse", { action_id: a.id }), "Customer refused (no cash moved); the rescue plan was recalculated")}>Customer refuses</Button>
            )}
            <Button size="sm" variant="secondary" busy={busy} onClick={() => run(() => apiPost("/api/sandbox/simulate/customer-pay", { action_id: a.id }), "Settlement received")}>
              Customer pays {money(remaining, cur)}
            </Button>
            <Button size="sm" variant="secondary" busy={busy} onClick={() => setPayOpen(true)}>Customer pays part…</Button>
          </div>
        </div>
      )}

      <Modal open={payOpen} onClose={() => setPayOpen(false)} title="Simulate a partial payment">
        <form
          className="space-y-4 text-sm"
          onSubmit={(e) => {
            e.preventDefault();
            const raw = payAmount.replace(/,/g, "").trim();
            const digits = digitsFor(cur);
            if (!new RegExp(`^\\d+(\\.\\d{1,${digits}})?$`).test(raw) || Number(raw) <= 0) {
              setFlash({ tone: "error", title: `Enter a positive amount with at most ${digits} decimals` });
              return;
            }
            const minor = Number(raw.split(".")[0]) * 10 ** digits + Number((raw.split(".")[1] ?? "").padEnd(digits, "0") || 0);
            setPayOpen(false);
            void run(() => apiPost("/api/sandbox/simulate/customer-pay", { action_id: a.id, amount_minor: minor }), "Partial settlement received");
          }}
        >
          <Field label={`Amount the customer pays (${cur})`} htmlFor={`pay-${a.id}`} hint={`Up to ${money(remaining, cur)}. Creates a signed sandbox settlement event.`}>
            <Input id={`pay-${a.id}`} inputMode="decimal" value={payAmount} onChange={(e) => setPayAmount(e.target.value)} placeholder="20000" data-autofocus aria-label={`Custom payment amount in ${cur}`} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setPayOpen(false)}>Cancel</Button>
            <Button type="submit" disabled={!payAmount}>Simulate payment</Button>
          </div>
        </form>
      </Modal>

      {open && (
        <ApprovalDialog
          action={a}
          open
          onClose={() => setOpen(false)}
          onDone={() => {
            setOpen(false);
            setFlash({ tone: "ok", title: `Approved ${money(a.amount_minor, cur)}`, text: "Bound to this exact amount, recipient version and policy. Single use; expires in 30 minutes. It is not sent until you press Send or Submit." });
            void Promise.all([onChanged(a.id), reload()]);
            refreshCounts();
          }}
        />
      )}
    </li>
  );
}

type CpData = { counterparties: Counterparty[]; changes: { id: string; counterparty_id: string; old_account_ref: string; new_account_ref: string; status: string; created_at: string }[] };

function Recipients({ onChanged, setFlash }: { onChanged: (follow?: string) => Promise<void>; setFlash: (f: Flash) => void }) {
  const { me } = useSession();
  const [data, setData] = useState<CpData | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [newRef, setNewRef] = useState("");
  const [applying, setApplying] = useState<string | null>(null);

  const load = useCallback(async () => setData(await apiGet<CpData>("/api/counterparties")), []);
  useEffect(() => {
    void load();
  }, [load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      setFlash({ tone: "ok", title: ok });
    } catch (e) {
      setFlash(describeError(e));
    }
    await Promise.all([load(), onChanged()]);
  }

  if (!data) return null;
  return (
    <Card id="recipients" title="Recipients" description="Only verified sandbox accounts can be paid. Any change to account details revokes existing approvals for that recipient.">
      <ul className="divide-y divide-line">
        <ListPresence>{data.counterparties.map((c) => (
          <motion.li {...itemMotion} key={c.id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm first:pt-0">
            <div className="min-w-0">
              <p className="font-medium">{c.name} <span className="font-normal text-muted">{label(c.kind)}</span></p>
              <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                <code>{c.account_ref}</code> version {c.version}
                {c.verified ? <Badge tone="ok" icon={CheckCircle2}>Verified</Badge> : <Badge tone="danger">Unverified</Badge>}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {!c.verified && me.role === "owner" && <Button size="sm" variant="secondary" onClick={() => act(() => apiPost(`/api/counterparties/${c.id}/verify`), "Verified against the sandbox registry")}>Verify</Button>}
              <Button size="sm" variant="ghost" onClick={() => { setEditing(editing === c.id ? null : c.id); setNewRef(""); }}>Request account change</Button>
            </div>
            {editing === c.id && (
              <form className="flex w-full flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); void act(() => apiPost(`/api/counterparties/${c.id}/changes`, { new_account_ref: newRef }), "Change requested; awaits owner review"); setEditing(null); }}>
                <div className="min-w-48 flex-1"><Input value={newRef} onChange={(e) => setNewRef(e.target.value)} placeholder="SBX-PAYROLL-0002" aria-label="New account reference" required /></div>
                <Button type="submit" variant="secondary">Submit change request</Button>
              </form>
            )}
          </motion.li>
        ))}</ListPresence>
      </ul>
      {data.changes.length > 0 && (
        <div className="mt-4 border-t border-line pt-4">
          <h3 className="mb-2 text-sm font-medium">Account change requests</h3>
          <ul className="space-y-2 text-sm">
            <ListPresence>{data.changes.map((ch) => (
              <motion.li {...itemMotion} key={ch.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2">
                <span className="flex flex-wrap items-center gap-2"><code>{ch.old_account_ref}</code> → <code>{ch.new_account_ref}</code> <Badge tone={ch.status === "pending" ? "warn" : ch.status === "applied" ? "ok" : "neutral"}>{ch.status === "pending" ? "Awaiting owner" : label(ch.status)}</Badge></span>
                {ch.status === "pending" && me.role === "owner" && <Button size="sm" variant="danger" onClick={() => setApplying(ch.id)}>Apply change</Button>}
              </motion.li>
            ))}</ListPresence>
          </ul>
        </div>
      )}
      <Modal open={!!applying} onClose={() => setApplying(null)} title="Apply this account change?">
        <div className="space-y-4 text-sm">
          <p>Every existing approval for this recipient will be revoked, and the new account must be verified before anything can be paid to it.</p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setApplying(null)}>Keep current details</Button>
            <Button variant="danger" onClick={() => { const id = applying!; setApplying(null); void act(() => apiPost(`/api/counterparty-changes/${id}/apply`), "Change applied; affected approvals revoked"); }}>Apply and revoke approvals</Button>
          </div>
        </div>
      </Modal>
    </Card>
  );
}

function Policies({ setFlash }: { setFlash: (f: Flash) => void }) {
  const { me } = useSession();
  const [list, setList] = useState<Policy[] | null>(null);
  const load = useCallback(async () => setList(await apiGet<Policy[]>("/api/policies")), []);
  useEffect(() => {
    void load();
  }, [load]);
  if (!list) return null;
  return (
    <Card id="policies" title="Policies" description="Your rules in plain words, and how FlowSense reads them. Activating a new version invalidates approvals made under the old one.">
      <ul className="space-y-3">
        <ListPresence>{list.map((p) => (
          <motion.li {...itemMotion} key={p.id} className={clsx("rounded-xl border p-4 text-sm", p.status === "active" ? "border-line" : "border-dashed border-line-strong")}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="flex items-center gap-2 font-medium">Version {p.version} <Badge tone={p.status === "active" ? "ok" : p.status === "proposed" ? "warn" : "neutral"}>{p.status === "proposed" ? "Proposed, not active" : label(p.status)}</Badge></p>
              {p.status === "proposed" && me.role === "owner" && (
                <Button
                  size="sm"
                  disabled={p.interpretation.questions.length > 0}
                  onClick={async () => {
                    try {
                      await apiPost(`/api/policies/${p.id}/activate`);
                      setFlash({ tone: "ok", title: `Policy v${p.version} activated`, text: "Approvals made under older policy versions can no longer be sent." });
                    } catch (e) {
                      setFlash(describeError(e));
                    }
                    await load();
                  }}
                >
                  Confirm &amp; activate
                </Button>
              )}
            </div>
            <p className="mt-2 text-muted">“{p.source_text}”</p>
            <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
              {p.interpretation.fields.map((f) => (
                <div key={f.field}>
                  <dt className="flex items-center gap-1.5 text-xs text-muted">{f.field} <span className="text-faint">({f.source})</span></dt>
                  <dd>{f.value}</dd>
                </div>
              ))}
            </dl>
            {p.interpretation.ambiguities.length > 0 && <p className="mt-3 text-xs text-warn">Assumptions: {p.interpretation.ambiguities.join(" ")}</p>}
            {p.interpretation.questions.length > 0 && <p className="mt-2 text-xs text-danger">Open questions: {p.interpretation.questions.join(" ")}</p>}
          </motion.li>
        ))}</ListPresence>
      </ul>
    </Card>
  );
}
