"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { AuthLayout, CodeInput, PasswordInput, useCooldown } from "@/components/auth";
import { Button, Field, Input, Notice } from "@/components/ui";
import { ApiError, apiPost } from "@/lib/api";
import { emailProblem } from "@/lib/validate";

type Msg = { tone: "info" | "error" | "warn" | "ok"; text: string } | null;

/** Two steps on one page: ask for a code, then enter it with the new password. */
export default function ForgotPasswordPage() {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const cool = useCooldown(60);

  async function sendCode(e?: FormEvent) {
    e?.preventDefault();
    if (busy) return;
    const p = emailProblem(email);
    if (p) return setFields({ email: p });
    setBusy(true);
    setMsg(null);
    setFields({});
    try {
      const r = await apiPost<{ message: string }>("/api/auth/password/forgot", { email });
      setMsg({ tone: "info", text: r.message });
      setStep(2);
      cool.start();
    } catch (err) {
      setMsg({ tone: err instanceof ApiError && err.code === "EMAIL_NOT_CONFIGURED" ? "warn" : "error", text: err instanceof ApiError ? err.message : "FlowSense could not be reached. Try again." });
    } finally {
      setBusy(false);
    }
  }

  async function reset(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const f: Record<string, string> = {};
    if (code.length !== 6) f.code = "Enter the 6-digit code from the email.";
    if (pw.length < 12) f.password = "Use at least 12 characters. A short phrase of several words works well.";
    if (pw2 !== pw) f.confirm_password = "The passwords don't match.";
    setFields(f);
    if (Object.keys(f).length) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiPost<{ message: string }>("/api/auth/password/reset", { email, code, password: pw, confirm_password: pw2 });
      setMsg({ tone: "ok", text: r.message });
      setStep(3);
    } catch (err) {
      if (err instanceof ApiError && err.body.fields) setFields(err.body.fields as Record<string, string>);
      else setMsg({ tone: "error", text: err instanceof ApiError ? err.message : "FlowSense could not be reached. Try again." });
      if (err instanceof ApiError && err.code === "CODE_INVALID") setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Reset your password"
      subtitle={step === 1 ? "We'll email you a 6-digit code. It works once and expires after 10 minutes." : step === 2 ? `Enter the code sent to ${email} and choose a new password.` : undefined}
      footer={<Link href="/login" className="font-medium text-accent-ink hover:underline">Back to sign in</Link>}
    >
      {step === 1 && (
        <form onSubmit={sendCode} className="space-y-4" noValidate>
          <Field label="Email address" htmlFor="email" error={fields.email}>
            <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          </Field>
          <div aria-live="polite">{msg && <Notice tone={msg.tone}>{msg.text}</Notice>}</div>
          <Button type="submit" busy={busy} className="w-full">Send code</Button>
        </form>
      )}
      {step === 2 && (
        <form onSubmit={reset} className="space-y-4" noValidate>
          <div aria-live="polite">{msg && <Notice tone={msg.tone}>{msg.text}</Notice>}</div>
          <Field label="6-digit code" htmlFor="code" error={fields.code}>
            <CodeInput id="code" value={code} onChange={setCode} invalid={!!fields.code} />
          </Field>
          <Field label="New password" htmlFor="password" error={fields.password} hint="At least 12 characters.">
            <PasswordInput id="password" autoComplete="new-password" required value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          <Field label="Confirm new password" htmlFor="confirm_password" error={fields.confirm_password}>
            <PasswordInput id="confirm_password" autoComplete="new-password" required value={pw2} onChange={(e) => setPw2(e.target.value)} />
          </Field>
          <Button type="submit" busy={busy} className="w-full">Change password</Button>
          <Button type="button" variant="quiet" disabled={cool.left > 0 || busy} onClick={() => sendCode()} className="w-full">
            {cool.left > 0 ? `Send a new code in ${cool.left}s` : "Send a new code"}
          </Button>
        </form>
      )}
      {step === 3 && (
        <div className="space-y-4">
          {msg && <Notice tone="ok">{msg.text}</Notice>}
          <Link href="/login" className="inline-flex min-h-10 w-full items-center justify-center rounded-lg bg-accent px-4 text-sm font-medium text-on-accent">Sign in</Link>
        </div>
      )}
    </AuthLayout>
  );
}
