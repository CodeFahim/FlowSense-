"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { AuthLayout, PasswordInput } from "@/components/auth";
import { Button, Field, Input, Notice } from "@/components/ui";
import { ApiError, apiGet, apiPost, isUnreachable, setCsrf } from "@/lib/api";
import type { Me } from "@/lib/types";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<{ text: string; code?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const [signedOut, setSignedOut] = useState(false);

  // Start waking the free API host while the user types, so sign-in doesn't wait for a cold start.
  useEffect(() => {
    setSignedOut(new URLSearchParams(window.location.search).has("signed_out"));
    apiGet("/api/health", { timeoutMs: 90_000 }).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!busy) return setSlow(false);
    const t = setTimeout(() => setSlow(true), 5000);
    return () => clearTimeout(t);
  }, [busy]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const me = await apiPost<Me>("/api/auth/login", { email, password });
      setCsrf(me.csrf_token);
      router.replace(me.needs_onboarding ? "/welcome" : "/");
    } catch (err) {
      setError(
        err instanceof ApiError && !isUnreachable(err)
          ? { text: err.message, code: err.code }
          : { text: "FlowSense could not be reached. Check your connection and try again. Free hosting can take about a minute to wake up." },
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Sign in"
      footer={<>New to FlowSense? <Link href="/signup" className="font-medium text-accent-ink hover:underline">Create an account</Link></>}
    >
      <form onSubmit={submit} className="space-y-4" noValidate={false}>
        <Field label="Email or username" htmlFor="email">
          <Input id="email" type="text" inputMode="email" autoComplete="username" autoCapitalize="none" spellCheck={false} required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
        </Field>
        <Field label="Password" htmlFor="password">
          <PasswordInput id="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div className="flex justify-end text-sm">
          <Link href="/forgot-password" className="font-medium text-accent-ink hover:underline">Forgot password?</Link>
        </div>
        {signedOut && !error && <Notice tone="info">Sign in to continue. If you were signed in before, that session has ended.</Notice>}
        {slow && <p role="status" className="text-sm text-muted">Still signing you in… the server may be waking up (up to about a minute).</p>}
        {error && (
          <Notice tone="error">
            {error.text}
            {error.code === "EMAIL_NOT_VERIFIED" && (
              <> <Link href={`/verify-email?email=${encodeURIComponent(email.includes("@") ? email : "")}`} className="font-medium underline">Enter or request a code</Link></>
            )}
          </Notice>
        )}
        <Button type="submit" busy={busy} className="w-full">Sign in</Button>
      </form>
      <p className="mt-4 text-xs text-muted">Reviewing the submission? The demo and Start-fresh account details are in the private submission notes.</p>
    </AuthLayout>
  );
}
