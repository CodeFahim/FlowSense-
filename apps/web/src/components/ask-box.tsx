"use client";

import { ArrowUp } from "lucide-react";
import { useState, type FormEvent } from "react";
import { BotMark, QuickActions, useAssistant } from "./assistant";
import { Button } from "./ui";

/**
 * The main way into FlowSense: ask the assistant. It already has the signed-in user's records, so the
 * quick questions need no typing. Asking opens the assistant panel (on phones, a bottom sheet).
 */
export function AskBox({ firstRun }: { firstRun?: boolean }) {
  const a = useAssistant();
  const [text, setText] = useState("");
  const live = a.status?.mode === "live";

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    a.ask(text);
    setText("");
  }

  return (
    <section aria-labelledby="ask-ai" className="rounded-2xl border border-accent/40 bg-surface p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent text-on-accent"><BotMark className="h-6 w-6" /></span>
        <div className="min-w-0">
          <h2 id="ask-ai" className="text-lg font-semibold">{firstRun ? "Not sure where to start? Ask me." : "Ask your AI assistant"}</h2>
          <p className="mt-0.5 text-sm text-muted">
            {firstRun
              ? "I'll walk you through setup one step at a time and only ask for what's missing."
              : "It already knows your cash, invoices, bills and deadlines, so you don't need to type them."}
            {a.status && !live && <> {a.status.mode === "demo" ? "This server uses the offline demo assistant (a fixed set of questions, not a language model)." : "The AI assistant is turned off on this server."}</>}
          </p>
        </div>
      </div>
      <div className="mt-4">
        {firstRun ? (
          <Button variant="secondary" onClick={() => a.ask("How do I get started?")}>How do I get started?</Button>
        ) : (
          <QuickActions onAsk={a.ask} disabled={a.busy} />
        )}
      </div>
      <form onSubmit={submit} className="mt-3 flex items-end gap-2 rounded-xl border border-line-strong bg-surface p-1.5 focus-within:border-accent">
        <label htmlFor="ask-home" className="sr-only">Ask the assistant in your own words</label>
        <input
          id="ask-home"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={2000}
          placeholder={firstRun ? "e.g. What do I need to enter first?" : "Or ask in your own words, e.g. Can I pay salaries on Friday?"}
          className="min-h-10 min-w-0 flex-1 bg-transparent px-2 text-sm placeholder:text-faint focus-visible:outline-none"
        />
        <Button type="submit" disabled={!text.trim()} aria-label="Ask" className="h-10 w-10 shrink-0 rounded-lg px-0"><ArrowUp className="h-4 w-4" aria-hidden /></Button>
      </form>
    </section>
  );
}
