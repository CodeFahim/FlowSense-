"use client";

import { ArrowRight, Check, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import { label, money } from "@/lib/format";
import type { Action, Decision } from "@/lib/types";
import { Button, Modal, Notice, ReasonList, Spinner } from "./ui";

type Preflight = { decision: Decision; phase: string; available_minor: number; available_after_minor: number; reserve_minor: number };

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-medium text-muted">{title}</h3>
      <div className="text-sm">{children}</div>
    </section>
  );
}

export function ApprovalDialog({ action, open, onClose, onDone }: { action: Action; open: boolean; onClose: () => void; onDone: () => void }) {
  const [pf, setPf] = useState<Preflight | null>(null);
  const [result, setResult] = useState<{ ok: boolean; reasons: { code: string; message: string }[]; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const cur = action.currency;

  // Mounted only while open, so state starts fresh each time.
  useEffect(() => {
    apiGet<Preflight>(`/api/actions/${action.id}/preflight`).then(setPf, () => setPf(null));
  }, [action.id]);

  async function approve() {
    if (busy) return;
    setBusy(true);
    try {
      await apiPost(`/api/actions/${action.id}/approve`);
      setResult({ ok: true, reasons: [], message: "Approved. This approval is bound to the details above and can be used once." });
      onDone();
    } catch (e) {
      if (e instanceof ApiError) setResult({ ok: false, reasons: e.reasons, message: e.message });
      else setResult({ ok: false, reasons: [], message: "The approval did not complete. Refresh to see the current status." });
    } finally {
      setBusy(false);
    }
  }

  const isPayment = action.type !== "payment_request";
  const cp = action.counterparty;
  const denied = pf?.decision.outcome === "DENY";
  const amount = money(action.amount_minor, cur);
  const codes = new Set((pf?.decision.reasons ?? []).map((r) => r.code));
  const shortage = ["INSUFFICIENT_SETTLED_FUNDS", "RESERVE_BREACH", "PROTECTED_OBLIGATION_AT_RISK"].some((c) => codes.has(c));
  const recipient = ["RECIPIENT_UNVERIFIED", "RECIPIENT_VERSION_CHANGED", "RECIPIENT_UNKNOWN"].some((c) => codes.has(c));

  return (
    <Modal open={open} onClose={onClose} title={isPayment ? `Approve ${label(action.type).toLowerCase()}` : "Approve payment request"} wide>
      <div className="space-y-5">
        <div className="rounded-xl bg-sunken p-4">
          <p className="num text-2xl font-semibold tracking-[-0.01em]">{amount}</p>
          <p className="mt-1 text-sm text-muted">{action.purpose}</p>
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <Section title="What will happen">
            {isPayment
              ? <>After you approve, you submit it as a separate step. The sandbox bank then pays {cp?.name} from settled cash.</>
              : <>After you approve, you send it as a separate step. {cp?.name} is asked to pay{action.invoice ? ` toward ${action.invoice.number}` : ""}. No money moves until they pay.</>}
          </Section>
          <Section title={isPayment ? "Recipient" : "Customer"}>
            {cp?.name}
            <span className="block text-xs text-muted">Account <code>{cp?.account_ref}</code>, version {action.counterparty_version}, {cp?.verified ? "verified in the sandbox registry" : "NOT verified"}</span>
          </Section>
          <Section title="Cash and reserve">
            {!isPayment && "No change. A request or a promise is not cash; only a settled payment changes your balance."}
            {isPayment && !pf && "Checking…"}
            {isPayment && pf && (
              <span className="num">
                {money(pf.available_minor, cur)} available now, {" "}
                <span className={pf.available_after_minor < pf.reserve_minor ? "font-semibold text-danger" : "font-semibold text-ok"}>{money(pf.available_after_minor, cur)}</span> after.
                <span className="block text-xs text-muted">
                  Minimum reserve {money(pf.reserve_minor, cur)}: {pf.available_after_minor >= pf.reserve_minor ? "stays protected." : `broken by ${money(pf.reserve_minor - pf.available_after_minor, cur)}.`}
                </span>
              </span>
            )}
          </Section>
          <Section title="Fees, expiry and limits">
            No fees in the sandbox. The approval expires 30 minutes after you give it and can be used once. Changing the amount, recipient or policy cancels it.
          </Section>
        </div>

        <section className="space-y-2 border-t border-line pt-4">
          <h3 className="text-xs font-medium text-muted">Safety check right now</h3>
          {!pf && <Spinner label="Running safety checks" />}
          {pf && (
            <>
              <p className="flex items-center gap-2 text-sm font-medium">
                {denied ? <X className="h-4 w-4 text-danger" aria-hidden /> : <Check className="h-4 w-4 text-ok" aria-hidden />}
                Safety kernel pre-check: {denied ? "BLOCKED" : "passes"}
              </p>
              <ReasonList reasons={pf.decision.reasons} />
              {pf.decision.warnings.length > 0 && <ReasonList reasons={pf.decision.warnings} tone="warn" />}
              <details className="text-xs text-muted">
                <summary className="cursor-pointer">All {pf.decision.checks.length} checks</summary>
                <ul className="mt-1 grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                  {pf.decision.checks.map((c) => <li key={c.name}>{c.passed ? "✓" : "✗"} {label(c.name)}</li>)}
                </ul>
              </details>
            </>
          )}
        </section>

        {result && (
          <Notice tone={result.ok ? "ok" : "error"} title={result.ok ? "Approved" : "Blocked by the backend"}>
            <p>{result.message}</p>
            <ReasonList reasons={result.reasons} />
          </Notice>
        )}

        {denied && !result?.ok && (
          // Blocked: no approve button at all. The safety rules can't be bypassed; the way forward is to fix the cause.
          <div className="space-y-3 border-t border-line pt-4" data-testid="approval-blocked">
            <Notice tone="error" title="Can't be approved right now">
              The safety rules block this {isPayment ? "payment" : "request"} for the reasons above. Approving would give the same result: fix the cause first.
            </Notice>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={onClose}>Close</Button>
              {shortage ? (
                <Link href="/rescue" onClick={onClose} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-medium text-on-accent hover:bg-accent-hover">
                  Resolve cash shortage <ArrowRight className="h-4 w-4" aria-hidden />
                </Link>
              ) : recipient ? (
                <Link href="/approvals#recipients" onClick={onClose} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-medium text-on-accent hover:bg-accent-hover">
                  Review the recipient <ArrowRight className="h-4 w-4" aria-hidden />
                </Link>
              ) : null}
            </div>
          </div>
        )}

        {!denied && !result?.ok && (
          <div className="space-y-4 border-t border-line pt-4">
            <label className="flex items-start gap-3 text-sm">
              <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} className="mt-0.5 h-4 w-4 accent-[var(--accent)]" />
              <span>I approve exactly {amount} {isPayment ? `to ${cp?.account_ref}` : `as a request to ${cp?.name}`} for this purpose.</span>
            </label>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button onClick={approve} busy={busy} disabled={!confirm || !pf}>{`Approve ${amount}`}</Button>
            </div>
            <p className="text-xs text-muted">The backend checks everything again when you approve and when it is sent.</p>
          </div>
        )}
        {result?.ok && <div className="flex justify-end"><Button onClick={onClose}>Done</Button></div>}
      </div>
    </Modal>
  );
}
