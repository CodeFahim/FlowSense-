"use client";

import { useExitValue } from "@/components/motion";
import { RotateCcw, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useSession } from "@/components/shell";
import { useCurrencyItems } from "@/components/converter";
import { Badge, Button, Card, Combobox, Field, Input, Loading, Modal, Notice, PageHeader, ReasonList } from "@/components/ui";
import { ApiError, apiGet, apiPost, newIdempotencyKey, type Reason } from "@/lib/api";
import { errorText } from "@/lib/outcomes";
import type { Action, Me } from "@/lib/types";

type Scenarios = { enabled: boolean; current: string | null; currency: string | null; scenarios: { key: string; title: string; description: string; currency?: string; fixed_currency?: boolean }[] };
type Outcome = { i: number; status: number; text: string; reasons: Reason[] };

export default function DemoPage() {
  const { me, setMe, refreshCounts } = useSession();
  const [s, setS] = useState<Scenarios | null>(null);
  const [pick, setPick] = useState<string | null>(null);
  // Keeps the scenario name in the dialog while it animates closed.
  const pickShown = useExitValue(pick);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [burst, setBurst] = useState<Outcome[] | null>(null);
  // Books currency for the next reset: the demo keeps its books in it (any ISO 4217 currency).
  const [books, setBooks] = useState<string | null>(null);
  const items = useCurrencyItems();

  const load = useCallback(async () => {
    const sc = await apiGet<Scenarios>("/api/demo/scenarios");
    setS(sc);
    setBooks((b) => b ?? sc.currency ?? "USD");
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function reset() {
    if (!pick) return;
    setBusy(true);
    try {
      const r = await apiPost<Me & { message: string }>("/api/demo/reset", { scenario: pick, confirm: "RESET", currency: books ?? undefined });
      setMe(r);
      setMsg({ tone: "ok", text: r.message });
      setPick(null);
      setTyped("");
      setBurst(null);
      refreshCounts();
      await load();
    } catch (e) {
      setMsg({ tone: "error", text: errorText(e, "The reset did not complete.") });
    } finally {
      setBusy(false);
    }
  }

  // Three submissions at once, each with a FRESH idempotency key: shows the backend, not the button, prevents double payment.
  async function duplicateBurst() {
    setBusy(true);
    setBurst(null);
    try {
      const acts = await apiGet<Action[]>("/api/actions?status=approved");
      const payroll = acts.find((a) => a.type === "payroll_payment");
      if (!payroll) {
        setMsg({ tone: "error", text: "There is no approved payroll payment. Reset to “Duplicate payroll submissions” first." });
        return;
      }
      const results = await Promise.all(
        [1, 2, 3].map(async (i): Promise<Outcome> => {
          try {
            const r = await apiPost<{ status: string; settlement?: { detail?: string } }>(`/api/actions/${payroll.id}/execute`, {}, { "Idempotency-Key": newIdempotencyKey() });
            return { i, status: 200, text: r.settlement?.detail ?? r.status, reasons: [] };
          } catch (e) {
            return { i, status: e instanceof ApiError ? e.status : 0, text: errorText(e, "failed"), reasons: e instanceof ApiError ? e.reasons : [] };
          }
        }),
      );
      setBurst(results);
      refreshCounts();
    } finally {
      setBusy(false);
    }
  }

  if (!s) return <Loading what="Loading scenarios" />;
  const chosen = s.scenarios.find((x) => x.key === pickShown);
  const owner = me.role === "owner";

  return (
    <div className="space-y-6">
      <PageHeader title="Demo scenarios" description="Load a prepared situation to try FlowSense. The current demo data is archived, never deleted, and the other test business is not touched." />
      {!s.enabled && <Notice tone="warn">Demo controls are turned off in this environment.</Notice>}
      {s.enabled && !owner && <Notice tone="info">Only the business owner can reset scenarios or run the duplicate-submission test. You are signed in as staff.</Notice>}
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}

      <Card title="Books currency" description="The demo keeps its books in this currency: the same story (50,000 cash, 80,000 payroll, 10,000 reserve), with amounts in that currency. It applies when you reset a scenario below; a scenario that names its own currency keeps it.">
        <div className="max-w-sm">
          <Field label="Currency for the next reset" htmlFor="demo-books">
            <Combobox id="demo-books" items={items} value={books} onChange={setBooks} placeholder="Search currency" />
          </Field>
        </div>
      </Card>

      <ul className="grid gap-3 sm:grid-cols-2">
        {s.scenarios.map((x) => (
          <li key={x.key}>
            <section className="flex h-full flex-col rounded-xl border border-line bg-surface p-5" aria-labelledby={`sc-${x.key}`}>
              <div className="flex flex-wrap items-center gap-2">
                <h2 id={`sc-${x.key}`} className="font-semibold">{x.title}</h2>
                {s.current === x.key && <Badge tone="accent">Current</Badge>}
                <Badge>{x.fixed_currency ? x.currency : books ?? x.currency}</Badge>
              </div>
              <p className="mt-1.5 flex-1 text-sm text-muted">{x.description}</p>
              <Button className="mt-4 self-start" size="sm" variant="secondary" disabled={!s.enabled || !owner} onClick={() => setPick(x.key)}>
                <RotateCcw className="h-4 w-4" aria-hidden /> Reset to this scenario
              </Button>
            </section>
          </li>
        ))}
      </ul>

      <Card title="Duplicate-submission test" description="Sends three payroll submissions at the same moment, each with a new idempotency key, like a double click plus a retry from another tab. Exactly one should pay.">
        <Button onClick={duplicateBurst} busy={busy} disabled={!owner}><Zap className="h-4 w-4" aria-hidden /> Fire 3 concurrent submissions</Button>
        {burst && (
          <ul className="mt-4 space-y-2 text-sm">
            {burst.map((b) => (
              <li key={b.i} className="rounded-lg border border-line px-3 py-2">
                <p><span className="font-medium">Request {b.i}</span>: HTTP {b.status} — <span className={b.status === 200 ? "text-ok" : "text-danger"}>{b.text}</span></p>
                <ReasonList reasons={b.reasons} />
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Modal open={!!pick} onClose={() => setPick(null)} title="Reset demo data">
        <div className="space-y-4 text-sm">
          <p>Reset to <strong>{chosen?.title}</strong> with books in <strong>{chosen?.fixed_currency ? chosen.currency : books}</strong>? The current demo business is archived with its full history, and anyone else using it is signed out.</p>
          <Field label="Type RESET to confirm" htmlFor="reset-confirm">
            <Input id="reset-confirm" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" data-autofocus />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setPick(null)}>Cancel</Button>
            <Button variant="danger" disabled={typed !== "RESET"} busy={busy} onClick={reset}>Reset</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
