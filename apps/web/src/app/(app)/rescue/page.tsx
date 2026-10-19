"use client";

import clsx from "clsx";
import { ArrowRight, Check, ChevronDown, CircleSlash, Eye, RefreshCw, Star } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ForecastChart } from "@/components/forecast-chart";
import { useSession } from "@/components/shell";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, Field, Input, Loading, Notice, PageHeader, Select, Status, Tip } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/api";
import { dateOnly, dateTime, digitsFor, label, money } from "@/lib/format";
import { describeError, errorText } from "@/lib/outcomes";
import type { Action, Forecast, PlanComponent, Position, RescueCase } from "@/lib/types";

type Rescue = { position: Position; case: RescueCase | null };
type StageState = "done" | "current" | "upcoming" | "blocked";

const KIND: Record<string, { title: string; verb: string }> = {
  partial_collection: { title: "Ask a customer to pay part of an invoice", verb: "Ask the customer to pay part of what they owe" },
  financing: { title: "Short-term financing", verb: "Borrow from a lender" },
  supplier_extension: { title: "More time from a supplier", verb: "Ask a supplier to accept a later due date" },
};

/** Plain state of one action, from its backend status and the latest read-only safety check. */
function actionState(a: Action): string {
  if (a.status === "proposed") return a.current_check?.outcome === "DENY" ? "Blocked by safety rules" : "Needs approval";
  return (
    {
      approved: "Approved, ready to send",
      sent: "Request sent, awaiting acceptance",
      accepted: "Accepted, awaiting settlement",
      partially_settled: "Partly settled",
      submitted: "In progress at the sandbox bank",
      settled: "Settled",
      refused: "Refused",
      denied: "Blocked by safety rules",
      failed: "Failed at the sandbox bank",
      cancelled: "Cancelled",
      expired: "Expired",
    } as Record<string, string>
  )[a.status] ?? label(a.status);
}

/**
 * Rescue progress: each stage follows the real state of its own actions. The funding request (money in) and
 * the payment it protects (money out) are approved separately, so one can be done while the other waits.
 */
function stages(c: RescueCase | null, acts: Action[]): { name: string; state: StageState; note: string }[] {
  const target = c?.target?.description ?? "the essential payment";
  const names = ["Understand the shortage", "Review options", "Approve the funding request", "Wait for the money", `Approve and pay ${target}`, "Review the result"];
  if (!c) return names.map((name) => ({ name, state: "done" as StageState, note: "" }));
  const mine = acts.filter((a) => a.case_id === c.id && !["cancelled", "expired"].includes(a.status));
  const funding = mine.filter((a) => a.type === "payment_request");
  const paying = mine.filter((a) => a.type !== "payment_request" && (!c.target || a.obligation?.id === c.target.id));
  const any = (xs: Action[], ...st: string[]) => xs.some((a) => st.includes(a.status));
  const fundNote = (xs: Action[]) => xs.map(actionState).filter((v, i, all) => all.indexOf(v) === i).join(" · ");
  const resolved = c.status === "RESOLVED";
  const out: { state: StageState; note: string }[] = [{ state: "done", note: "" }, { state: "done", note: "" }];
  if (c.status === "INFEASIBLE") {
    out[1] = { state: "blocked", note: "No option fits your policy" };
    return names.map((name, i) => ({ name, ...(out[i] ?? { state: "upcoming", note: "" }) }));
  }
  // Funding approval.
  if (funding.length === 0) out.push({ state: "done", note: resolved ? "" : "No request needed" });
  else if (any(funding, "proposed", "approved")) out.push({ state: "current", note: fundNote(funding.filter((a) => ["proposed", "approved"].includes(a.status))) });
  else if (funding.every((a) => ["refused", "denied", "failed"].includes(a.status))) out.push({ state: "blocked", note: "Refused: recalculate the plan" });
  else out.push({ state: "done", note: "Approved and sent" });
  // Money arriving (settled cash only).
  const fundingSettled = funding.length > 0 && funding.every((a) => ["settled", "refused", "denied", "failed"].includes(a.status)) && any(funding, "settled");
  if (funding.length === 0 || fundingSettled) out.push({ state: "done", note: fundingSettled ? "Settled" : "" });
  else if (any(funding, "sent", "accepted", "partially_settled")) out.push({ state: "current", note: fundNote(funding.filter((a) => ["sent", "accepted", "partially_settled"].includes(a.status))) });
  else out.push({ state: "upcoming", note: "" });
  // The protected payment (approved separately, checked again when sent).
  if (resolved || any(paying, "settled")) out.push({ state: "done", note: "Paid" });
  else if (paying.length === 0) out.push({ state: "upcoming", note: "" });
  else {
    const p = paying[0];
    const waitingForMoney = out[3].state !== "done";
    out.push({ state: waitingForMoney && p.status === "proposed" ? "upcoming" : "current", note: waitingForMoney && p.status === "proposed" ? "Needs approval after the money settles" : actionState(p) });
  }
  out.push(resolved ? { state: "done", note: "Resolved" } : { state: "upcoming", note: "" });
  // Exactly one "Now": the first stage that is not done.
  let seen = false;
  return names.map((name, i) => {
    let { state } = out[i];
    if (state === "current" || state === "upcoming") {
      state = seen ? "upcoming" : "current";
      seen = true;
    }
    return { name, state, note: out[i].note };
  });
}

export default function RescuePlanPage() {
  const { me } = useSession();
  const toast = useToast();
  const cur = me.business.currency ?? undefined;
  const tz = me.business.timezone ?? undefined;
  const [r, setR] = useState<Rescue | null>(null);
  const [acts, setActs] = useState<Action[]>([]);
  const [planIdx, setPlanIdx] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [rescue, actions] = await Promise.all([apiGet<Rescue>("/api/rescue"), apiGet<Action[]>("/api/actions")]);
      setR(rescue);
      setActs(actions);
      setErr(null);
    } catch (e) {
      setErr(errorText(e, "The rescue plan could not be loaded."));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function recalc() {
    setBusy(true);
    try {
      await apiPost("/api/rescue/replan");
      setPlanIdx(0);
      await load();
      toast({ tone: "ok", title: "Plan recalculated", text: "Saved as a new plan version. Nothing was approved or sent." });
    } catch (e) {
      const n = describeError(e);
      toast({ tone: "error", title: n.title, text: n.text });
    } finally {
      setBusy(false);
    }
  }

  if (err) return <Notice tone="error" title="Could not load the rescue plan">{err}</Notice>;
  if (!r) return <Loading what="Loading the rescue plan" />;
  const p = r.position;
  const c = r.case;
  const plan = c?.plans[planIdx];
  const body = plan?.body;
  const recommended = new Set(body?.recommended?.components ?? []);
  const stepAmount = (id: string) => body?.steps.find((s) => s.component === id)?.amount_minor;
  const st = stages(c, acts);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Rescue plan"
        description="How FlowSense closes a cash shortfall: what is missing, which options fit your policy, and what happens next. FlowSense never assumes money, cooperation or loans it does not have."
        actions={
          c && c.status !== "RESOLVED" && (
            <Button variant="secondary" busy={busy} onClick={recalc}>
              <RefreshCw className="h-4 w-4" aria-hidden /> Recalculate plan
            </Button>
          )
        }
      />

      {me.business.simulated_clock && (
        <Notice tone="info" title="Dates use this sample's simulated clock">
          The business date here is {dateOnly(me.business.sandbox_now, tz)} ({tz}), not today&apos;s date. &quot;Expected&quot; dates are counted from that simulated date.
        </Notice>
      )}

      {c && (
        <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" aria-label="Rescue progress">
          {st.map((s, i) => (
            <li key={s.name} className={clsx("rounded-xl border px-3 py-2.5", s.state === "current" ? "border-accent bg-accent-soft" : s.state === "blocked" ? "border-transparent bg-danger-soft" : "border-line bg-surface")} aria-current={s.state === "current" ? "step" : undefined}>
              <p className="flex items-center gap-1.5 text-xs text-muted">
                {s.state === "done" ? <Check className="h-3.5 w-3.5 text-ok" aria-hidden /> : s.state === "blocked" ? <CircleSlash className="h-3.5 w-3.5 text-danger" aria-hidden /> : <span className="num">{i + 1}</span>}
                {s.state === "done" ? "Done" : s.state === "current" ? "Now" : s.state === "blocked" ? "Blocked" : "Next"}
              </p>
              <p className="mt-0.5 text-sm font-medium leading-snug">{s.name}</p>
              {s.note && <p className="mt-0.5 text-xs text-muted">{s.note}</p>}
            </li>
          ))}
        </ol>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        <Card title="1. The shortage" className="lg:col-span-2" description={p.gap_minor > 0 ? `FlowSense needs ${money(p.gap_minor, cur)} more to pay on time and keep the reserve.` : "Settled cash covers everything due and the reserve."}>
          <table className="num w-full text-sm">
            <caption className="sr-only">How the funding gap is calculated</caption>
            <tbody className="divide-y divide-line">
              {p.protected.map((i) => (
                <tr key={i.obligation_id}>
                  <td className="py-2 pr-2">{i.description}<br /><span className="text-xs text-muted">Due {dateTime(i.due_at, tz)}</span></td>
                  <td className="py-2 text-right">{money(i.open_minor, cur)}</td>
                </tr>
              ))}
              <tr><td className="py-2 pr-2">Plus the minimum reserve</td><td className="py-2 text-right">{money(p.reserve_minor, cur)}</td></tr>
              <tr><td className="py-2 pr-2">Minus settled cash available</td><td className="py-2 text-right">−{money(p.available_minor, cur)}</td></tr>
              <tr className="font-semibold"><td className="py-2.5 pr-2">Funding gap</td><td className={clsx("py-2.5 text-right", p.gap_minor > 0 ? "text-danger" : "text-ok")}>{money(p.gap_minor, cur)}</td></tr>
            </tbody>
          </table>
          <p className="mt-3 text-xs text-muted">Counts payments due by {dateTime(p.horizon_end, tz)}. Promised and expected money is left out until it settles.</p>
        </Card>

        <div className="space-y-6 lg:col-span-3">
          {!c && <Notice tone="ok" title="No rescue needed">FlowSense opens a rescue case automatically if a shortfall appears.</Notice>}
          {c && plan && body && (
            <Card
              title="2. Options and the chosen plan"
              description={c.status === "RESOLVED" ? "This rescue is complete." : body.feasible ? "The recommended option has the lowest cost and fewest dependencies. Alternatives are shown too." : "No option allowed by your policy covers the gap."}
              actions={
                c.plans.length > 1 && (
                  <label className="flex items-center gap-2 text-xs text-muted">
                    Plan version
                    <Select className="w-auto py-1 text-xs" value={planIdx} onChange={(e) => setPlanIdx(Number(e.target.value))}>
                      {c.plans.map((pl, i) => <option key={pl.id} value={i}>v{pl.version}{i === 0 ? " (current)" : ""}</option>)}
                    </Select>
                  </label>
                )
              }
            >
              <div className="space-y-4">
                {!body.feasible && (
                  <Notice tone="error" title="No safe plan">
                    <p>{body.summary}</p>
                    <p>Still missing: <strong className="num">{money(body.remaining_gap_minor, cur)}</strong>. FlowSense will not pay payroll by breaking the reserve or borrowing above your cost limit.</p>
                    <p className="text-sm">What you can do: add settled funds, agree new terms with the customer or lender outside FlowSense, or review the policy limits in Approvals.</p>
                  </Notice>
                )}
                {body.components.map((cp) => (
                  <OptionCard key={cp.id} c={cp} recommended={recommended.has(cp.id)} amount={stepAmount(cp.id)} currency={cur} />
                ))}
                {body.rejected.length > 0 && (
                  <div>
                    <h3 className="mb-2 text-sm font-medium text-muted">Not available ({body.rejected.length})</h3>
                    <ul className="space-y-2">
                      {body.rejected.map((cp) => (
                        <li key={cp.id} className="rounded-xl border border-line bg-sunken px-4 py-3 text-sm">
                          <p className="flex flex-wrap items-center gap-2 font-medium">
                            <CircleSlash className="h-4 w-4 text-muted" aria-hidden /> {KIND[cp.kind]?.title ?? label(cp.kind)}
                            {cp.counterparty && <span className="font-normal text-muted">{cp.counterparty}</span>}
                          </p>
                          <p className="mt-1 text-muted">{cp.reason}</p>
                          {cp.annualized_all_in_bp !== undefined && (
                            <p className="num mt-1 text-xs text-muted">Cost {money(cp.cost_minor, cur)}, about {(cp.annualized_all_in_bp / 100).toFixed(2)}% a year all-in</p>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </Card>
          )}

          {c && body && body.steps.length > 0 && c.status !== "RESOLVED" && (
            <Card title="3. What happens next" description="Every step needs the owner's approval, and is checked again by the safety rules when it is sent.">
              <ol className="space-y-3 text-sm">
                {body.steps.map((s, i) => (
                  <li key={s.component} className="flex gap-3">
                    <span className="num flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sunken text-xs font-semibold">{i + 1}</span>
                    <span>{KIND[s.kind]?.verb ?? label(s.kind)}: <strong className="num">{money(s.amount_minor, cur)}</strong>. Cash changes only when it settles.</span>
                  </li>
                ))}
                <li className="flex gap-3">
                  <span className="num flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sunken text-xs font-semibold">{body.steps.length + 1}</span>
                  <span>Pay {c.target?.description} ({money(c.target?.amount_minor, cur)}) once that money has settled.</span>
                </li>
              </ol>
              <Link href="/approvals" className="mt-4 inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-medium text-on-accent hover:bg-accent-hover">
                Go to Approvals <ArrowRight className="h-4 w-4" aria-hidden />
              </Link>
            </Card>
          )}
        </div>
      </div>

      {c && (
        <details className="group rounded-xl border border-line bg-surface">
          <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-2 px-5 text-sm font-medium">
            <span className="flex items-center gap-2">Case history <Status value={c.status} /></span>
            <ChevronDown className="h-4 w-4 text-muted transition-transform group-open:rotate-180" aria-hidden />
          </summary>
          <ol className="space-y-2 border-t border-line px-5 py-4 text-sm">
            {c.transitions.map((t, i) => (
              <li key={i} className="flex flex-wrap gap-x-2">
                <span className="font-medium">{t.from ? `${label(t.from)} → ${label(t.to)}` : label(t.to)}</span>
                <span className="text-muted">{t.reason}</span>
              </li>
            ))}
            {plan && <li className="pt-2 text-xs text-muted">Plan v{plan.version} trigger: {plan.trigger}. Method: {plan.body.method}</li>}
          </ol>
        </details>
      )}

      <WhatIf currency={cur} />
    </div>
  );
}

function OptionCard({ c, recommended, amount, currency }: { c: PlanComponent; recommended: boolean; amount?: number; currency?: string }) {
  const k = KIND[c.kind] ?? { title: label(c.kind), verb: label(c.kind) };
  return (
    <article className={clsx("rounded-xl border p-4 text-sm", recommended ? "border-accent bg-accent-soft/50" : "border-line")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{k.title}{c.counterparty && <span className="font-normal text-muted"> with {c.counterparty}</span>}</h3>
        {recommended ? <Badge tone="accent" icon={Star}>Recommended</Badge> : <Badge>Alternative</Badge>}
      </div>
      <p className="mt-1.5">{k.verb}{c.invoice ? ` (${c.invoice})` : ""}.</p>
      <dl className="num mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        <div><dt className="text-xs text-muted">Amount</dt><dd className="font-medium">{money(amount ?? c.max_contribution_minor, currency)}</dd></div>
        <div><dt className="text-xs text-muted">Earliest expected</dt><dd>{c.expected_by ? dateOnly(c.expected_by) : "—"}</dd></div>
        <div><dt className="text-xs text-muted">Cost</dt><dd>{c.cost_minor ? money(c.cost_minor, currency) : "None"}</dd></div>
        <div><dt className="flex items-center gap-1 text-xs text-muted">Certainty <Tip text="How much depends on other people. 'High uncertainty' means the money may not arrive in time." /></dt><dd>{c.uncertainty === "low" ? "Higher" : c.uncertainty === "medium" ? "Medium" : "Lower"}</dd></div>
      </dl>
      {c.basis && <p className="mt-3 text-muted">{c.basis}</p>}
      {c.dependencies && c.dependencies.length > 0 && (
        <p className="mt-2 text-xs text-muted">Depends on: {c.dependencies.join("; ")}.</p>
      )}
      {c.evidence && c.evidence.length > 0 && <p className="mt-1 text-xs text-muted">Evidence: {c.evidence.join(", ")}</p>}
    </article>
  );
}

function WhatIf({ currency }: { currency?: string }) {
  const [days, setDays] = useState(14);
  const [delay, setDelay] = useState(0);
  const [pct, setPct] = useState(100);
  const [emergency, setEmergency] = useState("0");
  const [fc, setFc] = useState<Forecast | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = useCallback(
    async (q: { days: number; delay: number; pct: number; emergency: string }) => {
      const raw = q.emergency.replace(/,/g, "").trim() || "0";
      if (!/^\d+(\.\d+)?$/.test(raw)) {
        setErr("The emergency expense must be a plain number, such as 5000.");
        return;
      }
      const em = Math.round(Number(raw) * 10 ** digitsFor(currency ?? ""));
      try {
        setFc(await apiGet<Forecast>(`/api/forecast?days=${q.days}&delay_days=${q.delay}&collection_pct=${q.pct}&emergency_minor=${em}&emergency_day=1`));
        setErr(null);
      } catch (e) {
        setErr(errorText(e, "The preview could not be calculated."));
      }
    },
    [currency],
  );
  useEffect(() => {
    void run({ days: 14, delay: 0, pct: 100, emergency: "0" });
  }, [run]);

  function submit(e: FormEvent) {
    e.preventDefault();
    void run({ days, delay, pct, emergency });
  }

  return (
    <Card id="forecast" title="Preview a scenario" description="Try “what if” questions on the forecast. This is a simulation: it does not save anything or change any record.">
      <form onSubmit={submit} className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="Horizon" htmlFor="wi-days">
          <Select id="wi-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 14, 30, 60].map((d) => <option key={d} value={d}>{d} days</option>)}
          </Select>
        </Field>
        <Field label="Customer pays late by" htmlFor="wi-delay"><Input id="wi-delay" type="number" min={0} max={60} value={delay} onChange={(e) => setDelay(Number(e.target.value))} /></Field>
        <Field label="Share collected (%)" htmlFor="wi-pct"><Input id="wi-pct" type="number" min={0} max={100} value={pct} onChange={(e) => setPct(Number(e.target.value))} /></Field>
        <Field label={`Surprise expense (${currency})`} htmlFor="wi-em"><Input id="wi-em" inputMode="decimal" value={emergency} onChange={(e) => setEmergency(e.target.value)} /></Field>
        <div className="flex items-end"><Button type="submit" variant="secondary" className="w-full"><Eye className="h-4 w-4" aria-hidden /> Preview</Button></div>
      </form>
      {err && <Notice tone="error" className="mb-4">{err}</Notice>}
      {fc ? (
        <div className="space-y-4">
          <ForecastChart baseline={fc.baseline} scenario={fc.scenario} currency={currency ?? fc.currency} />
          <div className="grid gap-3 text-sm sm:grid-cols-3">
            <div className="rounded-xl bg-sunken p-3"><p className="text-xs text-muted">Lowest expected balance</p><p className="num mt-0.5 font-semibold">{money(fc.baseline.min_expected_minor, currency)} → {money(fc.scenario.min_expected_minor, currency)}</p></div>
            <div className="rounded-xl bg-sunken p-3"><p className="text-xs text-muted">First day below reserve (expected)</p><p className="mt-0.5 font-semibold">{fc.baseline.first_reserve_breach_expected ?? "None"} → {fc.scenario.first_reserve_breach_expected ?? "None"}</p></div>
            <div className="rounded-xl bg-sunken p-3"><p className="text-xs text-muted">First day below reserve (settled only)</p><p className="mt-0.5 font-semibold">{fc.baseline.first_reserve_breach_settled ?? "None"}</p></div>
          </div>
          <details className="text-sm">
            <summary className="cursor-pointer font-medium text-muted hover:text-ink">Assumptions and dated money movements</summary>
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-muted">{fc.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            <ul className="num mt-2 space-y-0.5 text-xs text-muted">
              {fc.scenario.inflows.map((f, i) => <li key={i}>{f.date}: money in, {f.label} ({f.class}) +{money(f.amount_minor, currency)}</li>)}
              {fc.scenario.outflows.map((f, i) => <li key={"o" + i}>{f.date}: money out, {f.label} −{money(f.amount_minor, currency)}</li>)}
            </ul>
          </details>
        </div>
      ) : (
        <Loading what="Calculating the preview" />
      )}
    </Card>
  );
}
