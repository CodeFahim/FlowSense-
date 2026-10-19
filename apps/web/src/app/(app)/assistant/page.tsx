"use client";

import { AssistantPanel, BotMark } from "@/components/assistant";
import { useSession } from "@/components/shell";

/** Full-page view of the same conversation the floating assistant uses. */
export default function AssistantPage() {
  const { openAdd } = useSession();
  return (
    <div className="mx-auto flex h-[calc(100dvh-10rem)] max-w-3xl flex-col overflow-hidden rounded-2xl border border-line bg-surface">
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <BotMark className="h-6 w-6 text-accent" />
        <h1 className="text-lg font-semibold">AI assistant</h1>
      </header>
      <AssistantPanel
        onDraft={(d) =>
          openAdd(d.record, { counterparty: d.fields.counterparty, description: d.fields.description, amount: d.fields.amount, due_date: d.fields.due_date, reference: d.fields.reference })
        }
      />
    </div>
  );
}
