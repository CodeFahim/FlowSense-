"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useAssistant } from "@/components/assistant";
import { useSession } from "@/components/shell";
import { Card, PageHeader } from "@/components/ui";

const TERMS: [string, string][] = [
  ["Money available now", "Money that has actually arrived in your account. FlowSense only uses this to decide whether a payment is safe."],
  ["Money customers owe you", "Unpaid invoices (Money in). They are not cash until the customer pays."],
  ["Money you need to pay", "Bills, payroll and other commitments (Money out). Recording a bill never pays it."],
  ["Essential payment", "A payment that must be made on time, such as payroll or rent. FlowSense protects these first."],
  ["Cash reserve", "Money you always want left in the account after payments. Payments that would break it are refused."],
  ["Projected shortage", "Essential payments due soon + your reserve − money available now. Promised money is never counted."],
  ["Needs approval / Ready to send", "A payment or payment request is prepared and waits for the owner to approve it, then to send it. Safety rules are checked again at each step."],
  ["In progress / Done", "Sent and waiting for the customer or the sandbox bank, then settled, refused or cancelled."],
  ["Sandbox", "Everything is simulated: no real bank, payment or message is ever used."],
];

const TOUR: { title: string; text: string; href: string }[] = [
  { title: "Home", text: "How you're doing, what is missing and the one thing to do next.", href: "/" },
  { title: "+ Add", text: "The button at the top of every page: cash, invoices, bills, payroll, customers, documents.", href: "/?add=invoice" },
  { title: "Money in and Money out", text: "Your invoices and bills, with due dates and status.", href: "/money-in" },
  { title: "Rescue plan", text: "If money runs short, the options that fit your rules, cheapest and safest first.", href: "/rescue" },
  { title: "Approvals", text: "Nothing is paid or sent until you approve it here.", href: "/approvals" },
  { title: "AI assistant", text: "The round bot button on every page. It reads your records and explains; it can't pay or send.", href: "/assistant" },
];

export default function HelpPage() {
  const { me } = useSession();
  const a = useAssistant();
  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader title="Help" description="Plain-language answers. Ask the assistant if something isn't covered." />
      <Card title="A two-minute tour">
        <ol className="space-y-3">
          {TOUR.map((t, i) => (
            <li key={t.title} className="flex items-start justify-between gap-3">
              <span className="flex min-w-0 gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sunken text-xs font-semibold">{i + 1}</span>
                <span><span className="block font-medium">{t.title}</span><span className="block text-sm text-muted">{t.text}</span></span>
              </span>
              <Link href={t.href} className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-accent-ink hover:underline">Open <ArrowRight className="h-4 w-4" aria-hidden /></Link>
            </li>
          ))}
        </ol>
      </Card>
      <Card title="What the words mean">
        <dl className="space-y-3 text-sm">
          {TERMS.map(([k, v]) => <div key={k}><dt className="font-medium">{k}</dt><dd className="text-muted">{v}</dd></div>)}
        </dl>
      </Card>
      <Card title="Still stuck?">
        <p className="text-sm text-muted">The assistant can tell you where to enter something or explain a figure, using your own records in {me.business.name || "your business"}.</p>
        <button type="button" onClick={() => a.setOpen(true)} className="mt-3 inline-flex min-h-10 items-center rounded-lg bg-accent px-4 text-sm font-medium text-on-accent">Ask the assistant</button>
      </Card>
    </div>
  );
}
