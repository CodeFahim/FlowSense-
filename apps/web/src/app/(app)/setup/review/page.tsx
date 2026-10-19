"use client";

import { CircleCheck, CircleDashed, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { recordsChanged } from "@/components/records";
import { useSession } from "@/components/shell";
import { Button, Field, Input, Notice, PageHeader, Select } from "@/components/ui";
import { apiPost } from "@/lib/api";
import { describeError } from "@/lib/outcomes";

/** Step 5: show what is known and missing, then the owner confirms the reserve (no preset value) and analyses. */
export default function ReviewPage() {
  const router = useRouter();
  const { me, reload } = useSession();
  const cur = me.business.currency;
  const [reserve, setReserve] = useState("");
  const [horizon, setHorizon] = useState("14");
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const steps = me.setup.steps.filter((s) => s.key !== "review");
  const missing = steps.filter((s) => !s.done);

  if (me.business.kind !== "workspace") return <Notice tone="info">Sample workspaces use their scenario&apos;s policy.</Notice>;
  if (!cur) return <Notice tone="info">First <Link href="/setup/business" className="underline">set up your business</Link>.</Notice>;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await apiPost("/api/policy/basic", { reserve, currency: cur, protection_horizon_days: Number(horizon), per_payment_limit: limit || null, max_financing_pct: null });
      recordsChanged();
      await reload();
      router.push("/");
    } catch (e2) {
      setErr(describeError(e2).text ?? describeError(e2).title);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader title="Review your position" description="Step 5 of setup." />
      <section className="rounded-2xl border border-line bg-surface p-5">
        <h2 className="font-semibold">What FlowSense knows</h2>
        <ul className="mt-3 space-y-2 text-sm">
          {steps.map((s) => (
            <li key={s.key} className="flex items-center gap-2">
              {s.done ? <CircleCheck className="h-4 w-4 text-ok" aria-hidden /> : <CircleDashed className="h-4 w-4 text-muted" aria-hidden />}
              <span>{s.title}{s.none_confirmed ? ": none" : s.done ? "" : ": missing"}</span>
              {!s.done && <Link href={s.href} className="ml-auto text-xs font-medium text-accent-ink hover:underline">Add</Link>}
            </li>
          ))}
        </ul>
        {missing.length > 0 && <p className="mt-3 text-sm text-warn">You can confirm the reserve now, but the analysis stays marked incomplete until the missing items are entered or marked as none.</p>}
      </section>
      <form onSubmit={submit} className="space-y-5 rounded-2xl border border-line bg-surface p-5 sm:p-6">
        <ShieldCheck className="h-6 w-6 text-accent" aria-hidden />
        <div>
          <h2 className="font-semibold">Your safety rules</h2>
          <p className="mt-1 text-sm text-muted">FlowSense refuses any payment that would break these. The reserve has no default: you choose it. You can change these later in Settings.</p>
        </div>
        <Field label={`Cash reserve to always keep (${cur})`} htmlFor="rv-reserve" hint="Money that must stay in the account after payments. Enter 0 if you don't want a reserve.">
          <Input id="rv-reserve" inputMode="decimal" required value={reserve} onChange={(e) => setReserve(e.target.value)} placeholder="e.g. 1,000.00" className="num" />
        </Field>
        <Field label="Protect essential payments due within" htmlFor="rv-horizon">
          <Select id="rv-horizon" value={horizon} onChange={(e) => setHorizon(e.target.value)}>
            <option value="7">The next 7 days</option>
            <option value="14">The next 14 days</option>
            <option value="30">The next 30 days</option>
          </Select>
        </Field>
        <Field label={`Largest single payment allowed (${cur})`} htmlFor="rv-limit" optional hint="Leave empty for no limit.">
          <Input id="rv-limit" inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="e.g. 20,000.00" className="num" />
        </Field>
        <p className="text-xs text-muted">Borrowing is not allowed by default. Payments only go to recipients you have verified.</p>
        {err && <Notice tone="error">{err}</Notice>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => router.push("/setup")} disabled={busy}>Back</Button>
          <Button type="submit" busy={busy} disabled={me.role !== "owner"}>Confirm and analyse my business</Button>
        </div>
      </form>
    </div>
  );
}
