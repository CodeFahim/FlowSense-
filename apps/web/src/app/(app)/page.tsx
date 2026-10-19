"use client";

import { AlertTriangle, ArrowRight, CalendarClock, ChevronDown, CircleCheck, CircleDashed, Info } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AskBox } from "@/components/ask-box";
import { ForecastChart } from "@/components/forecast-chart";
import { ListPresence, Rise, Stagger, itemMotion, motion } from "@/components/motion";
import { useRecordsChanged } from "@/components/records";
import { useSession } from "@/components/shell";
import { Badge, Card, Empty, Loading, Notice, Rows, Stat, isKnownStatus, Status } from "@/components/ui";
import { apiGet } from "@/lib/api";
import { eventTitle } from "@/lib/events";
import { dateOnly, dateTime, money } from "@/lib/format";
import { errorText } from "@/lib/outcomes";
import type { Dashboard, Forecast, Home, Setup } from "@/lib/types";

type SetupOnly = { setup_required: true; setup: Setup; currency: null };

const TONE = {
  incomplete: { badge: "neutral" as const, Icon: CircleDashed },
  ok: { badge: "ok" as const, Icon: CircleCheck },
  warn: { badge: "warn" as const, Icon: AlertTriangle },
  danger: { badge: "danger" as const, Icon: AlertTriangle },
};

export default function HomePage() {
  const { me } = useSession();
  const [d, setD] = useState<Dashboard | SetupOnly | null>(null);
  const [fc, setFc] = useState<Forecast | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const dash = await apiGet<Dashboard | SetupOnly>("/api/dashboard");
      setD(dash);
      setErr(null);
      if (!("setup_required" in dash && dash.setup_required)) apiGet<Forecast>("/api/forecast?days=14").then(setFc, () => setFc(null));
    } catch (e) {
      setErr(errorText(e, "Home could not be loaded."));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  useRecordsChanged(load);

  if (err) return <Notice tone="error" title="Could not load Home">{err}</Notice>;
  if (!d) return <Loading what="Loading Home" />;

  if ("setup_required" in d && d.setup_required) return <FirstRun setup={d.setup} />;
  const dash = d as Dashboard;
  const h = dash.home;
  const p = dash.position;
  const cur = dash.currency;
  const tz = me.business.timezone ?? "UTC";
  const t = TONE[h.status.tone];
  const next = h.tasks[0];
  const cashKnown = p.cash_status === "declared";
  const upcoming = dash.obligations.filter((o) => o.paid_minor < o.amount_minor);
  // Measured from the business date (the simulated date in demos), not the device clock.
  const in30 = new Date(h.data.business_date).getTime() + 31 * 864e5;
  const dueSoon = upcoming.filter((o) => new Date(o.due_at).getTime() <= in30);
  const dueSoonTotal = dueSoon.reduce((n, o) => n + (o.amount_minor - o.paid_minor), 0);
  const month = dash.this_month;

  return (
    <div className="space-y-8">
      <section aria-labelledby="status">
        <p className="text-sm text-muted">Dashboard · {me.business.name}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Badge tone={t.badge} icon={t.Icon}>{h.status.label}</Badge>
          <span className="text-xs text-muted">
            {h.data.simulated_clock ? `Simulated business date ${dateOnly(h.data.business_date)} (${tz})` : `Today ${dateOnly(h.data.business_date)} (${tz})`}
            {cashKnown && h.data.cash_as_of && ` · cash as of ${dateOnly(h.data.cash_as_of)}`}
          </span>
        </div>
        <h1 id="status" className="mt-3 max-w-3xl text-[1.55rem] leading-[1.25] font-semibold tracking-[-0.015em] text-balance sm:text-[1.9rem]">{h.status.sentence}</h1>
      </section>

      <AskBox />

      <Stagger as="section" aria-label="This month" className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Stat label="Money received this month" value={money(month?.money_in_minor ?? 0, cur)} sub={<Link href="/money" className="font-medium text-accent-ink hover:underline">See cash flow history</Link>} tip="Payments that actually arrived this month (settled): payments you recorded and sandbox settlements. A balance you declare or correct is existing cash, not money received, so it isn't counted here." />
        <Stat label="Money paid out this month" value={money(month?.money_out_minor ?? 0, cur)} sub={<Link href="/money-out" className="font-medium text-accent-ink hover:underline">Add an expense</Link>} tip="Payments that actually left this month (settled). Balance corrections are not counted." />
        <Stat label="Bills due in the next 30 days" value={money(dueSoonTotal, cur)} sub={`${dueSoon.length} payment${dueSoon.length === 1 ? "" : "s"}${dueSoon[0] ? ` · next ${dateOnly(dueSoon[0].due_at, tz)}` : ""}`} tip="Unpaid bills and payroll due within 30 days. Nothing is paid without your approval." />
      </Stagger>

      <Stagger as="section" aria-label="Key figures" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" gap={0.05}>
        <Stat label="Money available now" value={cashKnown ? money(p.available_minor, cur) : "Not entered"} tone={cashKnown ? undefined : "warn"} sub={cashKnown ? "Settled cash not already set aside for payments in progress" : <Link href="/setup/cash" className="font-medium text-accent-ink hover:underline">Add available cash</Link>} tip="Settled cash: the balance you declared (not bank-verified) plus payments recorded or settled in the sandbox. Invoices and promises are not included." />
        <Stat label={`Essential payments by ${dateOnly(p.horizon_end, tz)}`} value={money(p.protected_total_minor, cur)} sub="Payroll and payments you marked essential" tip="Essential payments due inside your protection window (from your reserve settings)." />
        <Stat label="Money customers owe you" value={money(dash.receivables.outstanding_minor, cur)} sub={dash.receivables.overdue_minor > 0 ? <span className="text-warn">{money(dash.receivables.overdue_minor, cur)} overdue · not cash yet</span> : `${dash.receivables.count} open invoice${dash.receivables.count === 1 ? "" : "s"} · not cash yet`} tip="Invoices still unpaid. They count as cash only when payment arrives." />
        <Stat
          label="Projected shortage"
          value={!cashKnown || !h.data.policy ? "Can't calculate yet" : money(p.gap_minor, cur)}
          tone={!cashKnown || !h.data.policy ? "warn" : p.gap_minor > 0 ? "danger" : undefined}
          sub={!cashKnown ? "Needs your available cash" : !h.data.policy ? "Needs your cash reserve" : p.gap_minor > 0 ? `Needed by ${dateTime(p.gap_date, tz)}` : "Nothing missing for essential payments"}
          tip="Essential payments + the reserve you keep − money available now. Expected or promised money is never counted."
        />
      </Stagger>

      <Equivalents eq={dash.display_equivalents} books={cur} />

      <details className="group rounded-xl border border-line bg-surface">
        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between px-5 text-sm font-medium">
          <span className="flex items-center gap-2"><Info className="h-4 w-4 text-muted" aria-hidden /> How is the shortage calculated?</span>
          <ChevronDown className="h-4 w-4 text-muted transition-transform group-open:rotate-180" aria-hidden />
        </summary>
        <div className="px-5 pb-4 text-sm">
          <Rows
            rows={[
              ["Essential payments due in the window", money(p.protected_total_minor, cur)],
              ["+ Cash reserve you keep", h.data.policy ? money(p.reserve_minor, cur) : "Not confirmed"],
              ["− Money available now", cashKnown ? money(p.available_minor, cur) : "Not entered"],
              ["= Projected shortage (never below zero)", !cashKnown || !h.data.policy ? "—" : money(p.gap_minor, cur)],
            ]}
          />
          <p className="mt-2 text-xs text-muted">Only settled cash counts. Invoices, accepted requests and expected payments are shown in the forecast but never reduce the shortage until the money arrives.</p>
        </div>
      </details>

      {next && (
        <Rise as="section" aria-labelledby="next" className="rounded-2xl border border-line bg-surface p-5 sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 max-w-2xl">
              <p className="text-sm font-medium text-accent-ink">Do this next</p>
              <h2 id="next" className="mt-1 text-lg font-semibold">{next.title}</h2>
              <p className="mt-1.5 text-sm text-muted">{next.why}</p>
            </div>
            <Link href={next.href} className="group inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-accent px-4 text-sm font-medium text-on-accent hover:bg-accent-hover hover:shadow-pop">
              {next.cta} <ArrowRight className="h-4 w-4 transition-transform duration-150 group-hover:translate-x-0.5" aria-hidden />
            </Link>
          </div>
        </Rise>
      )}

      {h.tasks.length > 1 && <TaskList tasks={h.tasks.slice(1)} cur={cur} />}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Upcoming payments" actions={<Link href="/money-out" className="text-sm font-medium text-accent-ink hover:underline">Money out</Link>}>
          {upcoming.length === 0 ? (
            <Empty icon={CircleCheck} title={h.data.money_out_none ? "You said there are no bills" : "No bills or payroll yet"} action={<Link href="/money-out?add=bill" className="text-sm font-medium text-accent-ink hover:underline">Add a bill</Link>} />
          ) : (
            <ul className="divide-y divide-line">
              <ListPresence>
              {upcoming.slice(0, 6).map((o) => (
                <motion.li key={o.id} {...itemMotion} className="flex items-start justify-between gap-3 py-3 first:pt-0">
                  <div className="min-w-0">
                    <p className="font-medium">{o.description}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                      <CalendarClock className="h-3.5 w-3.5" aria-hidden /> Due {dateTime(o.due_at, tz)}
                      {o.essential && <Badge tone="info">Essential</Badge>}
                    </p>
                  </div>
                  <p className="num shrink-0 text-right font-semibold">{money(o.amount_minor - o.paid_minor, cur)}</p>
                </motion.li>
              ))}
              </ListPresence>
            </ul>
          )}
        </Card>
        <Card title="Recent activity" actions={<Link href="/activity" className="text-sm font-medium text-accent-ink hover:underline">All activity</Link>}>
          {dash.recent_activity.length === 0 ? (
            <Empty title="Nothing yet">Saved records and decisions appear here.</Empty>
          ) : (
            <ul className="space-y-2.5">
              <ListPresence>
              {dash.recent_activity.slice(0, 6).map((e) => (
                <motion.li key={e.seq} {...itemMotion} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="min-w-0">{eventTitle(e)}</span>
                  {isKnownStatus(e.outcome) && <Status value={e.outcome} />}
                </motion.li>
              ))}
              </ListPresence>
            </ul>
          )}
        </Card>
      </div>

      {fc && cashKnown && (
        <details className="group rounded-xl border border-line bg-surface">
          <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between px-5 text-sm font-medium">
            Cash forecast, next 14 days
            <ChevronDown className="h-4 w-4 text-muted transition-transform group-open:rotate-180" aria-hidden />
          </summary>
          <div className="px-5 pb-5">
            <p className="mb-3 text-sm text-muted">Settled cash alone decides whether a payment is safe. The dashed line shows what happens if expected payments arrive.</p>
            <ForecastChart baseline={fc.baseline} currency={cur} height={240} />
          </div>
        </details>
      )}
    </div>
  );
}

function TaskList({ tasks, cur }: { tasks: Home["tasks"]; cur: string }) {
  return (
    <Card title="Also worth doing" description="In order of importance.">
      <ol className="divide-y divide-line">
        <ListPresence>
        {tasks.map((t) => (
          <motion.li key={t.key} {...itemMotion} className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
            <div className="min-w-0 max-w-xl">
              <p className="font-medium">{t.title}</p>
              <p className="text-sm text-muted">{t.why}</p>
              {(t.amount_minor !== undefined || t.date) && (
                <p className="mt-0.5 text-xs text-muted">{t.amount_minor !== undefined && <span className="num">{money(t.amount_minor, cur)}</span>}{t.date && ` · ${dateOnly(t.date)}`}</p>
              )}
            </div>
            <Link href={t.href} className="group inline-flex min-h-9 items-center gap-1 rounded-lg border border-line px-3 text-sm font-medium hover:bg-sunken">{t.cta} <ArrowRight className="h-4 w-4 transition-transform duration-150 group-hover:translate-x-0.5" aria-hidden /></Link>
          </motion.li>
        ))}
        </ListPresence>
      </ol>
    </Card>
  );
}

function FirstRun({ setup }: { setup: Setup }) {
  const done = setup.steps.filter((s) => s.done).length;
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <Badge tone="neutral" icon={CircleDashed}>Analysis incomplete</Badge>
        <h1 className="mt-3 text-[1.6rem] leading-tight font-semibold">Let&apos;s set up your business</h1>
        <p className="mt-2 text-muted">FlowSense needs a few facts before it can tell you whether upcoming payments are covered. Nothing is filled in for you, and you can stop and come back at any time.</p>
      </div>
      <AskBox firstRun />
      <Card title={`Setup: ${done} of ${setup.steps.length} done`}>
        <ol className="space-y-2">
          {setup.steps.map((s, i) => (
            <li key={s.key} className="flex items-center justify-between gap-3 rounded-lg border border-line px-4 py-3">
              <span className="flex min-w-0 items-center gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sunken text-xs font-semibold">{i + 1}</span>
                <span className="min-w-0"><span className="block font-medium">{s.title}</span><span className="block text-xs text-muted">{s.detail}</span></span>
              </span>
              {i === 0 ? <Link href={s.href} className="inline-flex min-h-9 shrink-0 items-center rounded-lg bg-accent px-3 text-sm font-medium text-on-accent">Start</Link> : <span className="text-xs text-faint">After step 1</span>}
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}

/**
 * Labelled approximate amounts in the user's display currency (Settings → Currency). Information only:
 * the books, approvals and the reserve stay in the accounting currency. Shown only when one is chosen.
 */
function Equivalents({ eq, books }: { eq: Dashboard["display_equivalents"] | undefined; books: string }) {
  if (!eq || eq.status === "same") return null;
  if (!eq.values) {
    return <p className="text-xs text-muted" data-testid="equivalents">No {eq.currency} rate is available right now, so approximate {eq.currency} amounts can&apos;t be shown. {eq.detail}</p>;
  }
  const v = eq.values;
  const fig = (minor: number) => money(minor, eq.currency, { mixed: true });
  return (
    <section aria-label={`Approximate amounts in ${eq.currency}`} data-testid="equivalents" className="rounded-xl border border-dashed border-line bg-surface px-4 py-3 text-sm">
      <p className="font-medium">Approximately in {eq.currency}</p>
      <p className="num mt-1 text-muted">
        Settled cash ≈ {fig(v.settled)} · Essential payments ≈ {fig(v.upcoming)} · Reserve ≈ {fig(v.reserve)} · Shortage ≈ {fig(v.gap)}
      </p>
      <p className="mt-1 text-xs text-muted">
        For information only, at the {eq.label ?? "reference"} rate{eq.rate ? ` 1 ${books} = ${eq.rate} ${eq.currency}` : ""}{eq.rate_date ? ` (${eq.rate_date})` : ""}. Your books, approvals and reserve stay in {books}.
      </p>
    </section>
  );
}
