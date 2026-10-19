"use client";

import { ArrowRight, CircleCheck, CircleDashed } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useSession } from "@/components/shell";
import { Badge, Button, Card, Notice, PageHeader } from "@/components/ui";
import { apiPost } from "@/lib/api";
import { describeError } from "@/lib/outcomes";

/** Resumable first-use checklist. Each step's state comes from stored facts, not from a wizard flag. */
export default function SetupPage() {
  const { me, reload, openAdd } = useSession();
  const s = me.setup;
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (me.business.kind !== "workspace") {
    return (
      <div className="space-y-6">
        <PageHeader title="Setup checklist" />
        <Notice tone="info">This is a sample workspace with synthetic data, so it is already set up. Switch to your own business from the account menu to set it up.</Notice>
      </div>
    );
  }
  const done = s.steps.filter((x) => x.done).length;

  async function none(step: "money_in" | "money_out", value: boolean) {
    setBusy(step);
    setErr(null);
    try {
      await apiPost("/api/setup/none", { step, none: value });
      await reload();
    } catch (e) {
      setErr(describeError(e).text ?? "Not saved.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title="Set up your business" description="Five short steps. Save and come back at any time; skip what doesn't apply, but say so, because 'none' and 'not entered yet' mean different things." />
      <div className="h-2 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-valuemin={0} aria-valuemax={s.steps.length} aria-valuenow={done} aria-label="Setup progress">
        <div className="bar-fill h-full bg-accent" style={{ width: `${(done / s.steps.length) * 100}%` }} />
      </div>
      {err && <Notice tone="error">{err}</Notice>}
      <ol className="space-y-3">
        {s.steps.map((st, i) => {
          const isNext = s.next === st.key;
          return (
            <li key={st.key}>
              <Card className={isNext ? "border-accent ring-1 ring-accent" : undefined}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 gap-3">
                    {st.done ? <CircleCheck className="mt-0.5 h-5 w-5 shrink-0 text-ok" aria-label="Done" /> : <CircleDashed className="mt-0.5 h-5 w-5 shrink-0 text-muted" aria-label="Not done" />}
                    <div className="min-w-0">
                      <h2 className="font-semibold">{i + 1}. {st.title}</h2>
                      <p className="text-sm text-muted">{st.detail}</p>
                      {st.none_confirmed && <Badge tone="neutral">You said there are none</Badge>}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {st.key === "money_in" && (
                      <>
                        <Button size="sm" variant={st.done ? "secondary" : "primary"} onClick={() => openAdd("invoice")} disabled={!me.business.configured}>Add invoice</Button>
                        {!st.done && <Button size="sm" variant="quiet" busy={busy === "money_in"} onClick={() => none("money_in", true)} disabled={!me.business.configured}>No customers owe me money</Button>}
                        {st.none_confirmed && <Button size="sm" variant="quiet" busy={busy === "money_in"} onClick={() => none("money_in", false)}>Undo</Button>}
                      </>
                    )}
                    {st.key === "money_out" && (
                      <>
                        <Button size="sm" variant={st.done ? "secondary" : "primary"} onClick={() => openAdd("bill")} disabled={!me.business.configured}>Add bill</Button>
                        <Button size="sm" variant="secondary" onClick={() => openAdd("payroll")} disabled={!me.business.configured}>Add payroll</Button>
                        {!st.done && <Button size="sm" variant="quiet" busy={busy === "money_out"} onClick={() => none("money_out", true)} disabled={!me.business.configured}>I have no bills to enter</Button>}
                        {st.none_confirmed && <Button size="sm" variant="quiet" busy={busy === "money_out"} onClick={() => none("money_out", false)}>Undo</Button>}
                      </>
                    )}
                    {(st.key === "profile" || st.key === "cash" || st.key === "review") && (
                      <Link href={st.href} className={`inline-flex min-h-8 items-center gap-1 rounded-lg px-2.5 text-[0.8rem] font-medium ${st.done ? "border border-line-strong" : "bg-accent text-on-accent"} ${st.key !== "profile" && !me.business.configured ? "pointer-events-none opacity-50" : ""}`}>
                        {st.done ? "Edit" : st.key === "review" ? "Review" : "Start"} <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                      </Link>
                    )}
                  </div>
                </div>
              </Card>
            </li>
          );
        })}
      </ol>
      {s.analysis_ready && (
        <Notice tone="ok" title="Setup complete" action={<Link href="/" className="font-medium underline">Go to Home</Link>}>FlowSense now checks your upcoming payments against your cash and reserve.</Notice>
      )}
    </div>
  );
}
