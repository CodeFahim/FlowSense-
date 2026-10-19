"use client";

import { ListPresence, itemMotion, motion } from "@/components/motion";
import { ChevronDown, Receipt as ReceiptIcon, ScrollText, Search, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Badge, Button, Card, Empty, Input, Loading, Notice, PageHeader, ReasonList, Status, Tabs, isKnownStatus } from "@/components/ui";
import { apiGet } from "@/lib/api";
import { EVENT_GROUPS, eventTitle } from "@/lib/events";
import { dateTime, label, money, timeZoneLabel } from "@/lib/format";
import { errorText } from "@/lib/outcomes";
import type { AuditEvent, Receipt } from "@/lib/types";

type Verify = { valid: boolean; count: number; head_hash?: string; first_invalid_seq: number | null; reason: string | null; trust_note: string };
type Ledger = { accounts: { code: string; name: string; kind: string; balance_minor: number }[]; balanced: boolean; total_debits_minor: number; total_credits_minor: number };
type View = "timeline" | "receipts" | "ledger";

const ACTOR: Record<string, string> = { user: "You or a team member", planner: "Rescue planner", assistant: "Assistant", sandbox_bank: "Sandbox bank", sandbox_counterparty: "Sandbox customer", demo_setup: "Demo setup", system: "FlowSense" };

export default function ActivityPage() {
  const [view, setView] = useState<View>("timeline");
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [receipts, setReceipts] = useState<Receipt[] | null>(null);
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [verify, setVerify] = useState<Verify | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [group, setGroup] = useState("all");
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (query = "") => {
    try {
      const [ev, rc, lg] = await Promise.all([
        apiGet<AuditEvent[]>(`/api/audit?limit=200${query ? `&q=${encodeURIComponent(query)}` : ""}`),
        apiGet<Receipt[]>("/api/receipts"),
        apiGet<Ledger>("/api/ledger"),
      ]);
      setEvents(ev);
      setReceipts(rc);
      setLedger(lg);
      setErr(null);
    } catch (e) {
      setErr(errorText(e, "Activity could not be loaded."));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function runVerify() {
    setVerifying(true);
    try {
      setVerify(await apiGet<Verify>("/api/audit/verify"));
    } catch (e) {
      setErr(errorText(e, "The audit chain could not be checked."));
    } finally {
      setVerifying(false);
    }
  }

  const filtered = useMemo(() => {
    const g = EVENT_GROUPS.find((x) => x.id === group) ?? EVENT_GROUPS[0];
    return (events ?? []).filter(g.match);
  }, [events, group]);

  if (err) return <Notice tone="error" title="Could not load activity">{err} <Button size="sm" variant="secondary" className="mt-2" onClick={() => load(q)}>Try again</Button></Notice>;
  if (!events || !receipts || !ledger) return <Loading what="Loading activity" />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Activity & Receipts"
        description="Everything FlowSense and your team did, in order, plus receipts for every settled payment. Records cannot be edited or deleted."
        actions={<Button variant="secondary" busy={verifying} onClick={runVerify}><ShieldCheck className="h-4 w-4" aria-hidden /> Check record integrity</Button>}
      />
      {verify && (
        <Notice tone={verify.valid ? "ok" : "error"} title={verify.valid ? `All ${verify.count} records are intact` : `Integrity check failed at record #${verify.first_invalid_seq ?? "?"}: ${verify.reason}`}>
          <p className="text-sm">{verify.trust_note}</p>
          {verify.head_hash && (
            <details className="text-xs text-muted"><summary className="cursor-pointer">Technical details</summary><p className="mt-1 break-all">Latest hash <code>{verify.head_hash}</code></p></details>
          )}
        </Notice>
      )}

      <Tabs<View>
        label="Activity views"
        value={view}
        onChange={setView}
        tabs={[
          { value: "timeline", label: "Timeline", count: events.length },
          { value: "receipts", label: "Receipts", count: receipts.length },
          { value: "ledger", label: "Ledger" },
        ]}
      />

      {view === "timeline" && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter activity">
              {EVENT_GROUPS.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  aria-pressed={group === g.id}
                  onClick={() => setGroup(g.id)}
                  className={`min-h-8 rounded-lg border px-3 text-sm ${group === g.id ? "border-ink bg-surface font-medium" : "border-line text-muted hover:text-ink"}`}
                >
                  {g.label}
                </button>
              ))}
            </div>
            <form
              className="relative ml-auto w-full sm:w-64"
              onSubmit={(e: FormEvent) => {
                e.preventDefault();
                void load(q);
              }}
            >
              <Search className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search records" aria-label="Search activity" className="pl-9" />
            </form>
          </div>
          <Card bodyClassName="px-0 pt-1 pb-1">
            {filtered.length === 0 ? (
              <Empty icon={ScrollText} title="No matching activity">Try another filter or search.</Empty>
            ) : (
              <ol className="divide-y divide-line">
                <ListPresence>{filtered.map((e) => (
                  <motion.li {...itemMotion} key={e.seq}>
                    <details className="group">
                      <summary className="flex min-h-14 cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 hover:bg-sunken">
                        <span className="num w-10 shrink-0 text-xs text-faint">#{e.seq}</span>
                        <span className="min-w-0 flex-1">
                          <span className="block font-medium">{eventTitle(e)}</span>
                          <span className="block text-xs text-muted">{ACTOR[e.actor_type] ?? label(e.actor_type)}, {dateTime(e.occurred_at)}</span>
                        </span>
                        {isKnownStatus(e.outcome) && <Status value={e.outcome} />}
                        <ChevronDown className="h-4 w-4 shrink-0 text-muted transition-transform group-open:rotate-180" aria-hidden />
                      </summary>
                      <div className="space-y-3 bg-sunken px-5 py-4 text-xs">
                        <ReasonList reasons={e.reasons} />
                        <pre className="overflow-auto font-mono whitespace-pre-wrap break-words">{JSON.stringify(e.payload, null, 2)}</pre>
                        <dl className="grid gap-1 break-all text-muted sm:grid-cols-2">
                          <div>Type <code>{e.event_type}</code></div>
                          <div>Action {e.action_id ?? "—"}</div>
                          <div>Approval {e.approval_id ?? "—"}</div>
                          <div>Policy version {e.policy_version ?? "—"}</div>
                          <div className="sm:col-span-2">Previous hash {e.prev_hash}</div>
                          <div className="sm:col-span-2">Hash {e.hash}</div>
                        </dl>
                      </div>
                    </details>
                  </motion.li>
                ))}</ListPresence>
              </ol>
            )}
          </Card>
          <p className="text-xs text-muted">Times show when each record was written (real time, {timeZoneLabel()}). Due dates elsewhere use the sandbox business clock.</p>
        </div>
      )}

      {view === "receipts" && (
        receipts.length === 0 ? (
          <Card><Empty icon={ReceiptIcon} title="No receipts yet">A receipt is created for every payment the sandbox bank settles.</Empty></Card>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2">
            {receipts.map((r) => {
              const c = r.content as Record<string, string | number | null>;
              const out = c.direction === "outgoing";
              return (
                <li key={r.id} className="rounded-xl border border-line bg-surface p-4 text-sm">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="font-semibold">{r.number}</p>
                      <Badge tone={out ? "info" : "ok"}>{out ? "Money out" : "Money in"}</Badge>
                    </div>
                    <p className="num text-lg font-semibold">{money(Number(c.amount_minor), String(c.currency))}</p>
                  </div>
                  <p className="mt-3">{String(c.purpose)}</p>
                  <p className="mt-1 text-muted">{out ? "To" : "From"} {String(c.counterparty)} <code className="text-xs">{String(c.account_ref)}</code></p>
                  <p className="mt-1 text-muted">Settled {dateTime(String(c.settled_at_business_time))}</p>
                  <details className="mt-3 text-xs text-muted">
                    <summary className="cursor-pointer">Proof details</summary>
                    <p className="mt-1 break-all">Bank reference <code>{String(c.provider_ref)}</code></p>
                    {c.approval_id && <p className="break-all">Owner approval <code>{String(c.approval_id)}</code></p>}
                    <p className="break-all">Receipt digest {r.digest}</p>
                  </details>
                </li>
              );
            })}
          </ul>
        )
      )}

      {view === "ledger" && (
        <Card title="Sandbox ledger" description="Double-entry balances. Entries are permanent; corrections are made with reversing entries." actions={ledger.balanced ? <Badge tone="ok">Balanced</Badge> : <Badge tone="danger">Not balanced</Badge>}>
          <div className="overflow-x-auto">
            <table className="num w-full text-sm">
              <caption className="sr-only">Trial balance</caption>
              <thead className="text-xs text-muted"><tr><th className="py-2 text-left font-medium">Account</th><th className="py-2 text-right font-medium">Balance</th></tr></thead>
              <tbody className="divide-y divide-line">
                {ledger.accounts.filter((a) => a.balance_minor !== 0).map((a) => (
                  <tr key={a.code}><td className="py-2 pr-3">{a.name} <span className="text-xs text-faint">{a.code}</span></td><td className="py-2 text-right">{money(a.balance_minor)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="num mt-3 text-xs text-muted">Debits {money(ledger.total_debits_minor)} equal credits {money(ledger.total_credits_minor)}.</p>
        </Card>
      )}
    </div>
  );
}
