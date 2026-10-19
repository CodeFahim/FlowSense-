"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { AuthLayout, CodeInput, PasswordInput, useCooldown } from "@/components/auth";
import { Button, Field, Input, Notice } from "@/components/ui";
import { ApiError, apiPost, setCsrf } from "@/lib/api";
import type { Me } from "@/lib/types";
import { emailProblem } from "@/lib/validate";

/** Step 3 of sign-up: type the 6-digit code from the email. The account is created and signed in on success. */
export default function VerifyEmailPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "info" | "error" | "warn"; text: string } | null>(null);
  const cool = useCooldown(60);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("email");
    if (q && !emailProblem(q)) setEmail(q);
  }, []);

  async function verify(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (emailProblem(email)) return setMsg({ tone: "error", text: emailProblem(email)! });
    if (code.length !== 6) return setMsg({ tone: "error", text: "Enter the 6-digit code from the email." });
    if (!password) return setMsg({ tone: "error", text: "Enter the password you chose when you signed up." });
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiPost<{ status: "signed_in" } & Me>("/api/auth/verify-code", { email, code, password });
      setCsrf(r.csrf_token);
      router.replace("/welcome");
    } catch (err) {
      setMsg({ tone: "error", text: err instanceof ApiError ? err.message : "FlowSense could not be reached. Check your connection and try again." });
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (emailProblem(email)) return setMsg({ tone: "error", text: "Enter your email address first." });
    setMsg(null);
    try {
      const r = await apiPost<{ message: string }>("/api/auth/verify-email/resend", { email });
      setMsg({ tone: "info", text: r.message });
      cool.start();
    } catch (err) {
      setMsg({ tone: err instanceof ApiError && err.code === "EMAIL_NOT_CONFIGURED" ? "warn" : "error", text: err instanceof ApiError ? err.message : "FlowSense could not be reached. Try again." });
    }
  }

  return (
    <AuthLayout
      title="Check your email"
      subtitle="We sent a 6-digit code to confirm the address belongs to you. It expires in 10 minutes."
      footer={<Link href="/login" className="font-medium text-accent-ink hover:underline">Back to sign in</Link>}
    >
      <form onSubmit={verify} className="space-y-4" noValidate>
        <Field label="Email address" htmlFor="email">
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
        </Field>
        <Field label="6-digit code" htmlFor="code" hint="From the email sent by “FlowSense — No Reply”. Check spam if you can't find it.">
          <CodeInput id="code" value={code} onChange={setCode} invalid={msg?.tone === "error"} />
        </Field>
        <Field label="Your password" htmlFor="password" hint="The one you chose on the sign-up form. This keeps your account safe if someone else used your email first.">
          <PasswordInput id="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div aria-live="polite">{msg && <Notice tone={msg.tone}>{msg.text}</Notice>}</div>
        <Button type="submit" busy={busy} className="w-full">Confirm and continue</Button>
        <p className="text-center text-xs text-muted">Didn&apos;t create this account, or forgot the password? <Link href="/forgot-password" className="font-medium text-accent-ink underline">Reset it with a code</Link>.</p>
        <Button type="button" variant="quiet" disabled={cool.left > 0} onClick={resend} className="w-full">
          {cool.left > 0 ? `Send a new code in ${cool.left}s` : "Send a new code"}
        </Button>
      </form>
    </AuthLayout>
  );
}
