"use client";

import { Building2, Check, Coins, FlaskConical, Lock, Store, Waves } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useCurrencyItems } from "@/components/converter";
import { useSession } from "@/components/shell";
import { Button, Combobox, Field, Notice } from "@/components/ui";
import { apiPost, setCsrf } from "@/lib/api";
import { describeError } from "@/lib/outcomes";
import type { AccountingCurrency, Me } from "@/lib/types";

export default function WelcomePage() {
  const router = useRouter();
  const { me, setMe, savePrefs, reload } = useSession();
  const own = me.business.kind === "workspace";
  // A private sample is rebuilt in the chosen currency (books really in it). The shared demo can't be
  // rebuilt for one visitor, so there the choice adds labelled approximate equivalents instead.
  const sample = me.business.kind === "sample";
  const needsBooks = me.business.kind === "demo" && me.role === "owner" && !me.accounting_currency.confirmed;
  const [step, setStep] = useState<"choose" | "display" | "books">(own ? "choose" : me.preferences.onboarded ? "books" : "display");
  const [display, setDisplay] = useState<string | null>(me.preferences.display_currency);
  const [books, setBooks] = useState(me.accounting_currency.currency);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const items = useCurrencyItems();

  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      const n = describeError(e);
      setErr(n.text ?? n.title);
    } finally {
      setBusy(null);
    }
  }

  async function signOut() {
    try {
      await apiPost("/api/auth/logout");
    } finally {
      setCsrf("");
      router.replace("/login");
    }
  }

  const tryDemo = () =>
    run("demo", async () => {
      const m = await apiPost<Me>("/api/workspaces/sample");
      setMe(m);
      setStep("display");
    });
  const startOwn = () =>
    run("own", async () => {
      await savePrefs({ complete_onboarding: true });
      router.replace("/setup/business");
    });
  const finishDisplay = (clear: boolean) =>
    run("display", async () => {
      if (sample) {
        if (!clear && display && display !== me.business.currency) {
          const m = await apiPost<Me>("/api/demo/reset", { scenario: me.business.scenario ?? "usd_primary", confirm: "RESET", currency: display });
          setMe(m);
        }
        await savePrefs({ clear: ["display_currency"], complete_onboarding: true });
        router.replace("/");
        return;
      }
      await savePrefs(clear || !display ? { clear: ["display_currency"], complete_onboarding: true } : { display_currency: display, complete_onboarding: true });
      if (needsBooks) setStep("books");
      else router.replace("/");
    });
  const confirmBooks = () =>
    run("books", async () => {
      await apiPost<AccountingCurrency>("/api/business/accounting-currency", { currency: books });
      await reload();
      router.replace("/");
    });

  const ac = me.accounting_currency;
  return (
    <div className="flex min-h-dvh flex-col items-center bg-canvas px-4 py-10 sm:py-16">
      <div className="w-full max-w-2xl">
        <div className="mb-8 flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-on-accent"><Waves className="h-[1.15rem] w-[1.15rem]" aria-hidden /></span>
          <span className="text-[1.05rem] font-semibold">FlowSense</span>
        </div>

        {step === "choose" && (
          <>
            <h1 className="text-2xl font-semibold">Welcome{me.user.display_name ? `, ${me.user.display_name.split(" ")[0]}` : ""}. How would you like to start?</h1>
            <p className="mt-2 text-muted">You can switch between a sample business and your own at any time from the account menu.</p>
            <div className="mt-6 grid gap-4 sm:grid-cols-2">
              <button type="button" onClick={startOwn} disabled={busy !== null} className="flex flex-col items-start rounded-2xl border-2 border-accent bg-surface p-5 text-left hover:bg-sunken disabled:opacity-60">
                <Store className="h-6 w-6 text-accent" aria-hidden />
                <span className="mt-3 text-lg font-semibold">Start my business</span>
                <span className="mt-1 text-sm text-muted">An empty workspace. You enter your own cash, invoices and bills. Nothing is filled in for you.</span>
                <span className="mt-4 inline-flex min-h-10 items-center rounded-lg bg-accent px-4 text-sm font-medium text-on-accent">{busy === "own" ? "Opening…" : "Start setup"}</span>
              </button>
              <button type="button" onClick={tryDemo} disabled={busy !== null} className="flex flex-col items-start rounded-2xl border border-line bg-surface p-5 text-left hover:bg-sunken disabled:opacity-60">
                <FlaskConical className="h-6 w-6 text-accent" aria-hidden />
                <span className="mt-3 text-lg font-semibold">Try the demo</span>
                <span className="mt-1 text-sm text-muted">A private sample business with synthetic data and a payroll shortfall to rescue. It never touches your own workspace.</span>
                <span className="mt-4 inline-flex min-h-10 items-center rounded-lg border border-line-strong px-4 text-sm font-medium">{busy === "demo" ? "Preparing sample…" : "Open sample"}</span>
              </button>
            </div>
          </>
        )}

        {step === "display" && (
          <div className="rounded-2xl border border-line bg-surface p-6 sm:p-8">
            <Coins className="h-6 w-6 text-accent" aria-hidden />
            <h1 className="mt-3 text-xl font-semibold">{sample ? "Which currency should the demo use?" : "Show approximate amounts in another currency?"}</h1>
            <p className="mt-1.5 text-sm text-muted">
              {sample
                ? `The sample business will keep its books in the currency you choose: the same story (50,000 cash, 80,000 payroll, 10,000 reserve), with every amount in that currency. It is set up again for you; you can change it later in Demo scenarios.`
                : <>Optional. {me.business.currency ? `This business keeps its books in ${me.business.currency}.` : ""} A display currency only adds labelled approximate equivalents; it never changes stored amounts, approvals or the ledger.</>}
            </p>
            <div className="mt-6">
              <Field label={sample ? "Demo currency" : "Display currency"} htmlFor="ob-display" optional={!sample} hint="Search by code, name or country.">
                <Combobox id="ob-display" items={items} value={display} onChange={setDisplay} placeholder="e.g. BDT, EUR" />
              </Field>
            </div>
            {err && <Notice tone="error" className="mt-4">{err}</Notice>}
            <div className="mt-8 flex flex-wrap items-center justify-end gap-2">
              <Button variant="secondary" busy={busy === "display"} onClick={() => finishDisplay(true)}>No, keep {me.business.currency ?? "the accounting currency"}</Button>
              <Button busy={busy === "display"} disabled={!display} onClick={() => finishDisplay(false)}>Use {display ?? "…"}</Button>
            </div>
          </div>
        )}

        {step === "books" && (
          <div className="rounded-2xl border border-line bg-surface p-6 sm:p-8">
            <Building2 className="h-6 w-6 text-accent" aria-hidden />
            <h1 className="mt-3 text-xl font-semibold">Confirm the accounting currency</h1>
            <p className="mt-1.5 text-sm text-muted">The currency the books, payments, approvals and reserve are kept in. Only the owner can confirm it.</p>
            <div className="mt-6 rounded-xl border border-line bg-sunken p-4">
              <p className="text-sm text-muted">{me.business.name}</p>
              {ac.locked ? (
                <>
                  <p className="mt-1 text-lg font-semibold">{ac.currency} <span className="font-normal text-muted">{ac.currency_name}</span></p>
                  <p className="mt-2 flex gap-2 text-sm text-muted"><Lock className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> This business already has records in {ac.currency}, so its accounting currency cannot change.</p>
                </>
              ) : (
                <div className="mt-2">
                  <Field label="Accounting currency" htmlFor="ob-books">
                    <Combobox id="ob-books" items={items} value={books} onChange={setBooks} placeholder="Search currency" />
                  </Field>
                </div>
              )}
            </div>
            {err && <Notice tone="error" className="mt-4">{err}</Notice>}
            <div className="mt-8 flex justify-end">
              <Button busy={busy === "books"} onClick={confirmBooks} disabled={!books}><Check className="h-4 w-4" aria-hidden /> Confirm {ac.locked ? ac.currency : books}</Button>
            </div>
          </div>
        )}
        {step === "choose" && err && <Notice tone="error" className="mt-4">{err}</Notice>}
        <p className="mt-6 text-center text-xs text-muted">Sandbox with synthetic money. No real banks, payments or messages.</p>
        {/* The welcome page hides the app header, so it offers its own way out. */}
        <p className="mt-2 text-center text-xs text-muted">
          Signed in as <span className="font-medium text-ink">{me.user.email}</span> ·{" "}
          <button type="button" onClick={signOut} className="font-medium text-accent-ink underline">Sign out</button>
        </p>
      </div>
    </div>
  );
}
