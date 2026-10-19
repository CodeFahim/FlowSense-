"use client";

import { CircleCheck, Wallet } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { useRecordsChanged } from "@/components/records";
import { useSession } from "@/components/shell";
import { Notice, PageHeader } from "@/components/ui";

/** Step 2: the cash form is the shared "Available cash" dialog; this page explains it and opens it once. */
export default function CashSetupPage() {
  const { me, openAdd } = useSession();
  const router = useRouter();
  const opened = useRef(false);
  const ready = me.business.configured && me.business.kind === "workspace" && me.role === "owner";
  useEffect(() => {
    // Open the form once on arrival. Closing it (X, Cancel, Esc) must leave it closed.
    if (ready && !opened.current) {
      opened.current = true;
      openAdd("cash");
    }
  }, [ready, openAdd]);
  // After a successful save the dialog closes itself; continue to the checklist, which shows the new figure.
  useRecordsChanged(() => router.push("/setup"));
  const step = me.setup.steps.find((s) => s.key === "cash");
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader title="Available cash" description="Step 2 of setup." />
      <div className="rounded-2xl border border-line bg-surface p-6">
        <Wallet className="h-6 w-6 text-accent" aria-hidden />
        <h2 className="mt-3 font-semibold">Money already in your account today</h2>
        <p className="mt-1 text-sm text-muted">
          FlowSense compares this with upcoming payments. If you have nothing in the account, enter 0. Until you enter a figure, FlowSense shows &quot;not entered&quot; and never assumes zero.
        </p>
        {step?.done && (
          <p className="mt-3 flex items-center gap-1.5 text-sm font-medium text-ok"><CircleCheck className="h-4 w-4" aria-hidden /> {step.detail}</p>
        )}
        {!me.business.configured ? (
          <Notice tone="info" className="mt-4">First <Link href="/setup/business" className="font-medium underline">set up your business</Link> (currency and timezone).</Notice>
        ) : me.business.kind !== "workspace" ? (
          <Notice tone="info" className="mt-4">Sample workspaces have fixed balances.</Notice>
        ) : me.role !== "owner" ? (
          <Notice tone="info" className="mt-4">Only the owner can record the cash balance.</Notice>
        ) : (
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" onClick={() => openAdd("cash")} className="inline-flex min-h-10 items-center rounded-lg bg-accent px-4 text-sm font-medium text-on-accent">{step?.done ? "Update available cash" : "Enter available cash"}</button>
            <button type="button" onClick={() => router.push("/setup")} className="inline-flex min-h-10 items-center rounded-lg border border-line-strong px-4 text-sm font-medium">Back to checklist</button>
          </div>
        )}
      </div>
    </div>
  );
}
