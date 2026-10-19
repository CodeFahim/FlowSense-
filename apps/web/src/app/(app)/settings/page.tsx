"use client";

import clsx from "clsx";
import { ArrowLeftRight, Building2, Check, KeyRound, Laptop, Lock, Moon, RotateCcw, ShieldOff, Sun, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useAssistant } from "@/components/assistant";
import { CodeInput, PasswordInput, useCooldown } from "@/components/auth";
import { RATE_STATUS, useCurrencyItems } from "@/components/converter";
import { useSession } from "@/components/shell";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, Combobox, Field, Input, Modal, Notice, PageHeader, Segmented, Select } from "@/components/ui";
import { ApiError, apiGet, apiPost, setCsrf } from "@/lib/api";
import { ACCENTS, type Accent, type Theme } from "@/lib/appearance";
import { dateTime, money, timeZoneLabel } from "@/lib/format";
import { describeError } from "@/lib/outcomes";
import type { FxCoverage, Me, Policy, Preferences } from "@/lib/types";

type Deletion = { allowed: boolean; protected: boolean; archive: string[]; leave: string[]; blocked: string[]; deleted: string[]; retained: string[] };
type Health = { mode: string; status: string; message?: string; provider?: string; model?: string };

export default function SettingsPage() {
  const { me, savePrefs, reload, openConverter } = useSession();
  const toast = useToast();
  const router = useRouter();
  const p = me.preferences;
  const items = useCurrencyItems();
  const [locales, setLocales] = useState<{ code: string; label: string }[]>([]);
  const [cov, setCov] = useState<FxCoverage | null>(null);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [killOpen, setKillOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const owner = me.role === "owner";

  useEffect(() => {
    apiGet<{ locales: { code: string; label: string }[] }>("/api/reference/locales").then((r) => setLocales(r.locales), () => undefined);
    apiGet<FxCoverage>("/api/fx/coverage").then(setCov, () => undefined);
    apiGet<Policy[]>("/api/policies").then((l) => setPolicy(l.find((x) => x.status === "active") ?? null), () => undefined);
    if (owner) apiGet<Health>("/api/assistant/health").then(setHealth, () => undefined);
  }, [owner]);

  async function save(change: Partial<Preferences> & { clear?: string[] }, what: string) {
    try {
      await savePrefs(change);
      toast({ tone: "ok", title: `${what} saved` });
    } catch (e) {
      const n = describeError(e);
      toast({ tone: "error", title: n.title, text: n.text });
    }
  }

  async function setKill(on: boolean) {
    setBusy(true);
    try {
      await apiPost("/api/kill-switch", { enabled: on });
      await reload();
      toast({ tone: on ? "warn" : "ok", title: on ? "Emergency stop is on" : "Emergency stop is off", text: on ? "New payments and requests are blocked." : "Submissions are allowed again; every safety check still applies." });
      setKillOpen(false);
    } catch (e) {
      const n = describeError(e);
      toast({ tone: "error", title: n.title, text: n.text });
    } finally {
      setBusy(false);
    }
  }

  const ac = me.accounting_currency;
  const b = me.business;

  return (
    <div className="max-w-3xl">
      <PageHeader title="Settings" description="Display preferences only change how things are shown. They never change amounts, approvals or records." />
      <nav aria-label="Settings sections" className="mb-6 flex flex-wrap gap-2 text-sm">
        {["business", "account", "display", "currency", "assistant", "safety"].map((id) => (
          <a key={id} href={`#${id}`} className="rounded-lg border border-line px-3 py-1.5 capitalize hover:bg-sunken">{id}</a>
        ))}
      </nav>
      <div className="space-y-6">
        <Card id="business" title="Business" description="The facts FlowSense uses for every amount and deadline.">
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div><dt className="text-muted">Name</dt><dd className="font-medium">{b.name || "Not named yet"}</dd></div>
            <div><dt className="text-muted">Workspace</dt><dd>{{ workspace: "Your business", demo: "Shared demo (synthetic)", sample: "Your private sample (synthetic)", isolation: "Test business" }[b.kind]}</dd></div>
            <div><dt className="text-muted">Place / type</dt><dd>{[b.place, b.business_type].filter(Boolean).join(" · ") || "—"}</dd></div>
            <div><dt className="text-muted">Country</dt><dd>{b.country ?? "Not set"}</dd></div>
            <div>
              <dt className="text-muted">Accounting currency</dt>
              <dd className="font-medium">{ac.currency ?? "Not confirmed"} {ac.currency_name && <span className="font-normal text-muted">{ac.currency_name}</span>}</dd>
              {ac.locked && <dd className="mt-0.5 flex gap-1 text-xs text-muted"><Lock className="h-3.5 w-3.5 shrink-0" aria-hidden /> Locked: records already exist in this currency.</dd>}
            </div>
            <div><dt className="text-muted">Business timezone</dt><dd className="font-medium">{b.timezone ?? "Not confirmed"}</dd></div>
          </dl>
          <div className="mt-4 flex flex-wrap gap-2">
            {owner && <Link href="/setup/business" className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line-strong px-3 text-sm font-medium hover:bg-sunken"><Building2 className="h-4 w-4" aria-hidden /> Edit business profile</Link>}
            {b.kind === "workspace" && <Link href="/setup" className="inline-flex min-h-9 items-center rounded-lg px-3 text-sm font-medium text-accent-ink hover:underline">Setup checklist</Link>}
          </div>
          {owner && b.kind === "workspace" && <StartOver />}
        </Card>

        <AccountSection me={me} />

        <Card id="display" title="Display" description="How numbers, dates and times look for you.">
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Number and date format" htmlFor="st-locale">
              <Select id="st-locale" value={p.number_locale ?? ""} onChange={(e) => save(e.target.value ? { number_locale: e.target.value } : { clear: ["number_locale"] }, "Format")}>
                <option value="">Same as this browser</option>
                {locales.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
              </Select>
            </Field>
            <div className="space-y-1.5">
              <p className="text-sm font-medium">Show times in</p>
              <Segmented<Preferences["time_display"]>
                label="Show times in"
                value={p.time_display}
                onChange={(v) => save({ time_display: v }, "Time display")}
                options={[{ value: "business", label: `Business (${b.timezone ?? "not set"})` }, { value: "device", label: "This device" }]}
              />
            </div>
          </div>
          <p className="num mt-4 rounded-lg bg-sunken px-3 py-2 text-sm text-muted">
            Preview: {b.currency ? money(123456789, b.currency) : "—"} · {dateTime(new Date().toISOString())} ({timeZoneLabel()})
          </p>
          <div className="mt-5 space-y-1.5">
            <p className="text-sm font-medium">Theme</p>
            <Segmented<Theme> label="Theme" value={p.theme} onChange={(v) => save({ theme: v }, "Theme")} options={[{ value: "system", label: "System", icon: Laptop }, { value: "light", label: "Light", icon: Sun }, { value: "dark", label: "Dark", icon: Moon }]} />
          </div>
          <div className="mt-4 space-y-1.5">
            <p className="text-sm font-medium" id="accent-label">Accent colour</p>
            <div role="radiogroup" aria-labelledby="accent-label" className="flex flex-wrap gap-2">
              {ACCENTS.map((a) => (
                <button key={a.id} type="button" role="radio" aria-checked={p.accent === a.id} onClick={() => save({ accent: a.id as Accent }, "Accent")} className={clsx("flex min-h-10 items-center gap-2 rounded-lg border px-3 text-sm", p.accent === a.id ? "border-ink font-medium" : "border-line hover:border-line-strong")}>
                  <span className="h-4 w-4 rounded-full" style={{ background: a.swatch }} aria-hidden />
                  {a.label}
                  {p.accent === a.id && <Check className="h-4 w-4" aria-hidden />}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted">Status colours (settled, warning, refused) keep their meaning whatever accent you pick.</p>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={() => save({ theme: "system", accent: "teal" }, "Appearance")}><RotateCcw className="h-4 w-4" aria-hidden /> Reset appearance</Button>
            <Button variant="quiet" size="sm" onClick={async () => { await savePrefs({ restart_onboarding: true }); router.push("/welcome"); }}>Run the welcome steps again</Button>
          </div>
        </Card>

        <Card id="currency" title="Currency" description="Approximate equivalents and the converter. Stored records always keep the accounting currency.">
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Display currency" htmlFor="st-currency" optional hint={p.display_currency ? "Shown as labelled approximate equivalents." : `None chosen: amounts show in ${b.currency ?? "the accounting currency"} only.`}>
              <Combobox id="st-currency" items={items} value={p.display_currency} onChange={(v) => save({ display_currency: v }, "Display currency")} placeholder="Search currency" />
            </Field>
            <div className="space-y-1.5">
              <p className="text-sm font-medium">Converter</p>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" size="sm" onClick={openConverter}><ArrowLeftRight className="h-4 w-4" aria-hidden /> Open converter</Button>
                {p.display_currency && <Button variant="quiet" size="sm" onClick={() => save({ clear: ["display_currency"] }, "Display currency")}>Remove display currency</Button>}
              </div>
            </div>
          </div>
          {cov && (
            <div className="mt-5 space-y-2 rounded-xl border border-line p-4 text-sm">
              <p className="flex flex-wrap items-center gap-2 font-medium">
                Exchange-rate coverage <Badge tone={RATE_STATUS[cov.status as keyof typeof RATE_STATUS]?.tone ?? "neutral"}>{RATE_STATUS[cov.status as keyof typeof RATE_STATUS]?.text ?? cov.status}</Badge>
              </p>
              <p className="text-muted">
                {cov.provider_label}: {cov.quoted_count} of {cov.selectable_count} selectable currencies quoted · updates {cov.refresh_frequency?.toLowerCase() ?? "—"}
                {cov.provider_as_of && ` · published ${dateTime(cov.provider_as_of)}`}
              </p>
              {cov.unavailable.length > 0 && (
                <details>
                  <summary className="cursor-pointer text-xs font-medium text-muted">Currencies without an automatic rate ({cov.unavailable.length})</summary>
                  <p className="mt-1 text-xs break-words text-muted">{cov.unavailable.join(", ")}</p>
                </details>
              )}
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted">{cov.notes.map((n) => <li key={n}>{n}</li>)}</ul>
              {cov.attribution && <p className="text-xs"><a href={cov.attribution.url} target="_blank" rel="noopener noreferrer" className="text-accent-ink underline">{cov.attribution.text}</a></p>}
            </div>
          )}
        </Card>

        <AssistantSection health={health} owner={owner} />

        <Card id="safety" title="Safety" description={b.name}>
          <div className="space-y-5">
            <div>
              <p className="text-sm font-medium">Cash reserve and limits</p>
              {policy ? (
                <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                  {policy.interpretation.fields.slice(0, 6).map((f) => <div key={f.field}><dt className="text-xs text-muted">{f.field}</dt><dd>{f.value}</dd></div>)}
                </dl>
              ) : (
                <p className="mt-1 text-sm text-muted">No reserve confirmed yet. FlowSense refuses every payment until one is confirmed.</p>
              )}
              {owner && b.kind === "workspace" && <Link href="/setup/review" className="mt-2 inline-flex text-sm font-medium text-accent-ink hover:underline">{policy ? "Change reserve and limits" : "Confirm your reserve"}</Link>}
            </div>
            <div className="border-t border-line pt-5">
              <p className="flex items-center gap-2 text-sm font-medium">Emergency stop {b.kill_switch ? <Badge tone="danger" icon={ShieldOff}>On</Badge> : <Badge>Off</Badge>}</p>
              <p className="mt-1 text-sm text-muted">Blocks every new payment and payment request immediately. Payments already in progress still complete.</p>
              {owner ? (
                <Button className="mt-3" variant={b.kill_switch ? "secondary" : "danger"} onClick={() => (b.kill_switch ? setKill(false) : setKillOpen(true))} busy={busy}>
                  <ShieldOff className="h-4 w-4" aria-hidden /> {b.kill_switch ? "Turn emergency stop off" : "Turn emergency stop on"}
                </Button>
              ) : (
                <Notice tone="info" className="mt-3">Only the owner can change the emergency stop, the reserve and the accounting currency.</Notice>
              )}
            </div>
          </div>
        </Card>
      </div>

      <Modal open={killOpen} onClose={() => setKillOpen(false)} title="Turn the emergency stop on?">
        <div className="space-y-4 text-sm">
          <p>New payments and payment requests will be refused until you turn it off. Nothing already submitted is cancelled, and reconciliation continues.</p>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="secondary" onClick={() => setKillOpen(false)}>Keep it off</Button>
            <Button variant="danger" busy={busy} onClick={() => setKill(true)}><ShieldOff className="h-4 w-4" aria-hidden /> Block new payments</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function StartOver() {
  const { setMe } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setErr(null);
    try {
      const m = await apiPost<Me>("/api/workspaces/start-over", { confirm: "START OVER" });
      setMe(m);
      router.replace("/setup/business");
    } catch (e) {
      setErr(describeError(e).text ?? describeError(e).title);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mt-5 border-t border-line pt-4">
      <p className="text-sm font-medium">Start over</p>
      <p className="text-sm text-muted">Archives this workspace (nothing is deleted; the history stays verifiable) and opens a new empty one.</p>
      <Button size="sm" variant="secondary" className="mt-2" onClick={() => setOpen(true)}>Start over with an empty workspace</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Start over?">
        <div className="space-y-4 text-sm">
          <p>This workspace will be archived and hidden. A new empty workspace replaces it. Type <strong>START OVER</strong> to confirm.</p>
          <Input aria-label="Type START OVER" value={text} onChange={(e) => setText(e.target.value)} />
          {err && <Notice tone="error">{err}</Notice>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="danger" busy={busy} disabled={text !== "START OVER"} onClick={go}>Archive and start over</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function AccountSection({ me }: { me: Me }) {
  const toast = useToast();
  const emailOn = me.email_delivery === "configured";
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [next2, setNext2] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const cool = useCooldown(60);

  // Step 1 (email configured): re-enter the current password; a code goes to your email while you wait.
  async function sendCode() {
    if (!cur) return setFields({ current_password: "Enter your current password first." });
    setBusy(true);
    setErr(null);
    setFields({});
    try {
      const r = await apiPost<{ message: string }>("/api/account/password/code", { current_password: cur });
      setCodeSent(r.message);
      cool.start();
    } catch (e2) {
      if (e2 instanceof ApiError && e2.body.fields) setFields(e2.body.fields as Record<string, string>);
      else setErr(e2 instanceof ApiError ? e2.message : "No code was sent. Try again.");
    } finally {
      setBusy(false);
    }
  }

  // Step 2: current password + code + new password.
  async function change(e: FormEvent) {
    e.preventDefault();
    const f: Record<string, string> = {};
    if (!cur) f.current_password = "Enter your current password.";
    if (next.length < 12) f.new_password = "Use at least 12 characters.";
    if (next2 !== next) f.confirm = "The passwords don't match.";
    if (emailOn && code.length !== 6) f.code = "Enter the 6-digit code from the email.";
    setFields(f);
    if (Object.keys(f).length) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await apiPost<{ message: string }>("/api/account/password", { current_password: cur, new_password: next, code: emailOn ? code : null });
      toast({ tone: "ok", title: "Password changed", text: r.message });
      setCur("");
      setNext("");
      setNext2("");
      setCode("");
      setCodeSent(null);
    } catch (e2) {
      if (e2 instanceof ApiError && e2.body.fields) setFields(e2.body.fields as Record<string, string>);
      else setErr(e2 instanceof ApiError ? e2.message : "Not changed. Try again.");
    } finally {
      setBusy(false);
    }
  }
  const managed = me.user.account_kind !== "user";
  return (
    <Card id="account" title="Account" description={me.user.email}>
      {me.user.username && <p className="mb-1 text-sm">Username <span className="font-medium">{me.user.username}</span> <span className="text-xs text-muted">(you can sign in with it)</span></p>}
      <p className="text-sm">
        Email {me.user.email_verified ? <Badge tone="ok">Confirmed</Badge> : <Badge tone="warn">Not confirmed</Badge>}
        {!me.user.email_verified && me.email_delivery === "not_configured" && <span className="ml-2 text-xs text-muted">Email delivery isn&apos;t configured on this server, so confirmation emails can&apos;t be sent.</span>}
      </p>
      {managed ? (
        <Notice tone="info" className="mt-4">This is a shared demo or test account. Its password is managed by the server operator, and it can&apos;t be deleted.</Notice>
      ) : (
        <>
          <form onSubmit={change} className="mt-4 space-y-4" noValidate>
            <p className="flex items-center gap-2 text-sm font-medium"><KeyRound className="h-4 w-4" aria-hidden /> Change password</p>
            <p className="text-xs text-muted">{emailOn ? "For your safety: your current password, then a 6-digit code we email you." : "Email delivery isn't configured on this server, so your current password alone confirms it's you."}</p>
            <Field label="Current password" htmlFor="pw-cur" error={fields.current_password}><PasswordInput id="pw-cur" autoComplete="current-password" required value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
            {emailOn && (
              <div className="space-y-2">
                <Button type="button" variant="secondary" busy={busy && !codeSent} disabled={cool.left > 0} onClick={sendCode}>
                  {cool.left > 0 ? `Send a new code in ${cool.left}s` : codeSent ? "Send a new code" : "Email me a code"}
                </Button>
                {codeSent && <Notice tone="info">{codeSent}</Notice>}
              </div>
            )}
            {(!emailOn || codeSent) && (
              <>
                {emailOn && <Field label="6-digit code" htmlFor="pw-code" error={fields.code}><CodeInput id="pw-code" value={code} onChange={setCode} invalid={!!fields.code} /></Field>}
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="New password" htmlFor="pw-new" hint="At least 12 characters." error={fields.new_password}><PasswordInput id="pw-new" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} /></Field>
                  <Field label="Confirm new password" htmlFor="pw-new2" error={fields.confirm}><PasswordInput id="pw-new2" autoComplete="new-password" required value={next2} onChange={(e) => setNext2(e.target.value)} /></Field>
                </div>
                <Button type="submit" busy={busy && (!emailOn || !!codeSent)}>Change password</Button>
              </>
            )}
            {err && <Notice tone="error">{err}</Notice>}
          </form>
          <DangerZone />
        </>
      )}
    </Card>
  );
}

function DangerZone() {
  const router = useRouter();
  const [info, setInfo] = useState<Deletion | null>(null);
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (open) apiGet<Deletion>("/api/account/deletion").then(setInfo, () => undefined);
  }, [open]);
  async function del() {
    setBusy(true);
    setErr(null);
    try {
      await apiPost("/api/account/delete", { password: pw, confirm: "DELETE MY ACCOUNT" });
      setCsrf("");
      router.replace("/login");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Not deleted. Try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-labelledby="danger" className="mt-6 rounded-xl border border-danger/40 p-4">
      <h3 id="danger" className="text-sm font-semibold text-danger">Danger zone</h3>
      <p className="mt-1 text-sm text-muted">Permanently delete your account and personal data.</p>
      <Button variant="danger" size="sm" className="mt-3" onClick={() => setOpen(true)}><Trash2 className="h-4 w-4" aria-hidden /> Delete my account</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Delete your account?">
        {!info ? (
          <p className="text-sm text-muted">Checking what would be deleted…</p>
        ) : (
          <div className="space-y-4 text-sm">
            {info.blocked.length > 0 && <Notice tone="error">Other people still use: {info.blocked.join(", ")}. Remove them or make someone else an owner first.</Notice>}
            <div>
              <p className="font-medium">Deleted</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted">{info.deleted.map((d) => <li key={d}>{d}</li>)}</ul>
            </div>
            {info.archive.length > 0 && <p><span className="font-medium">Archived workspaces:</span> {info.archive.join(", ")}</p>}
            <div>
              <p className="font-medium">Kept, and why</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted">{info.retained.map((d) => <li key={d}>{d}</li>)}</ul>
            </div>
            <Field label="Your password" htmlFor="del-pw" hint="Required to confirm it's you."><PasswordInput id="del-pw" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
            <Field label="Type DELETE MY ACCOUNT" htmlFor="del-confirm"><Input id="del-confirm" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" /></Field>
            {err && <Notice tone="error">{err}</Notice>}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
              <Button variant="danger" busy={busy} disabled={!info.allowed || !pw || confirm !== "DELETE MY ACCOUNT"} onClick={del}>Delete my account</Button>
            </div>
          </div>
        )}
      </Modal>
    </section>
  );
}

function AssistantSection({ health, owner }: { health: Health | null; owner: boolean }) {
  const a = useAssistant();
  const toast = useToast();
  const st = a.status;
  const healthText: Record<string, string> = {
    ok: "Connected",
    demo: "Offline demo assistant (no AI provider configured)",
    off: "Turned off",
    invalid_credentials: "API key rejected",
    billing: "No remaining credit",
    rate_limited: "Rate or usage limit reached",
    provider_down: "Provider problem",
    network: "Provider unreachable",
    timeout: "Provider timed out",
    model_not_found: "Model not found",
    permission: "Key not allowed for this model",
  };
  return (
    <Card id="assistant" title="AI assistant" description="Mode, history and limits.">
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div><dt className="text-muted">Mode</dt><dd><Badge tone={st?.mode === "live" ? "accent" : st?.mode === "off" ? "danger" : "warn"}>{st?.label ?? "…"}</Badge></dd></div>
        {owner && health && <div><dt className="text-muted">Connection</dt><dd>{healthText[health.status] ?? health.status}{health.provider ? ` · ${health.provider === "gemini" ? "Google Gemini" : "Anthropic"}` : ""}{health.model ? ` · ${health.model}` : ""}</dd></div>}
        <div><dt className="text-muted">History</dt><dd>Kept on the server for {st?.retention_days ?? 30} days, for your account in this workspace only</dd></div>
        <div><dt className="text-muted">Limits</dt><dd>{st ? `${st.limits.messages_per_day} messages a day · up to ${st.limits.max_tool_rounds} record checks per answer` : "…"}</dd></div>
      </dl>
      <p className="mt-3 text-xs text-muted">The assistant reads your records and FlowSense&apos;s calculations for this workspace. It cannot approve, pay, send, change policies or delete anything.</p>
      <Button size="sm" variant="secondary" className="mt-3" onClick={async () => { await a.clear(); toast({ tone: "ok", title: "Conversation history deleted" }); }}><Trash2 className="h-4 w-4" aria-hidden /> Delete my assistant history</Button>
    </Card>
  );
}
