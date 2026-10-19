"use client";

import clsx from "clsx";
import { Banknote, Plus, ReceiptText, Wallet } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "./shell";
import { Button } from "./ui";

const TABS = [
  { href: "/money", label: "History" },
  { href: "/money-in", label: "Money in" },
  { href: "/money-out", label: "Money out" },
  { href: "/money/forecast", label: "Sales forecast" },
];

/** The Money section: the main things to add, then the four views. Shown at the top of each Money page. */
export function MoneyTabs() {
  const path = usePathname();
  const { me, openAdd } = useSession();
  const ownCash = me.business.kind === "workspace" && me.role === "owner";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[1.6rem] leading-tight font-semibold">Money</h1>
          <p className="mt-1 text-sm text-muted">Everything that came in, went out, or is still expected.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => openAdd("bill")}><Banknote className="h-4 w-4" aria-hidden /> Add expense</Button>
          <Button variant="secondary" onClick={() => openAdd("invoice")}><ReceiptText className="h-4 w-4" aria-hidden /> Add invoice</Button>
          {ownCash && <Button variant="secondary" onClick={() => openAdd("cash")}><Wallet className="h-4 w-4" aria-hidden /> Update cash</Button>}
          <Button variant="quiet" onClick={() => openAdd("payroll")}><Plus className="h-4 w-4" aria-hidden /> Payroll</Button>
        </div>
      </div>
      <nav aria-label="Money views" className="-mx-1 flex gap-1 overflow-x-auto border-b border-line px-1">
        {TABS.map((t) => {
          const active = t.href === "/money" ? path === "/money" : path.startsWith(t.href);
          return (
            <Link key={t.href} href={t.href} aria-current={active ? "page" : undefined}
              className={clsx("-mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-medium", active ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink")}>
              {t.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
