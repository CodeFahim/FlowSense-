"use client";

import { Eye, EyeOff, Waves } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type InputHTMLAttributes, type ReactNode } from "react";
import { inputCls } from "./ui";

/**
 * Password field with a show/hide toggle. Masked by default. The toggle is type="button", so it never
 * submits the form or clears the value; paste and password managers work as with a plain input.
 */
export function PasswordInput({ id, ...props }: InputHTMLAttributes<HTMLInputElement> & { id: string }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="relative">
      <input id={id} {...props} type={shown ? "text" : "password"} className={`${inputCls} pr-11`} spellCheck={false} autoCapitalize="none" />
      <button
        type="button"
        onClick={() => setShown((s) => !s)}
        aria-label={shown ? "Hide password" : "Show password"}
        aria-pressed={shown}
        aria-controls={id}
        title={shown ? "Hide password" : "Show password"}
        className="absolute top-1/2 right-1 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-md text-muted hover:bg-sunken hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
      >
        {shown ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
      </button>
    </div>
  );
}

export function AuthLayout({ title, subtitle, children, footer }: { title: string; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-canvas">
      <div className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm">
          <Link href="/login" className="mb-8 flex items-center gap-2.5 rounded-lg" aria-label="FlowSense sign in">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent text-on-accent"><Waves className="h-5 w-5" aria-hidden /></span>
            <span>
              <span className="block text-lg leading-tight font-semibold">FlowSense</span>
              <span className="block text-sm text-muted">Cash-flow rescue for small businesses</span>
            </span>
          </Link>
          <main className="rounded-2xl border border-line bg-surface p-6">
            <h1 className="text-lg font-semibold">{title}</h1>
            {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
            <div className="mt-5">{children}</div>
          </main>
          {footer && <div className="mt-4 text-center text-sm text-muted">{footer}</div>}
          <p className="mt-5 rounded-xl border border-dashed border-line-strong px-4 py-3 text-xs text-muted">
            <span className="font-medium text-ink">Sandbox.</span> Synthetic money only: no real banks, payments or messages.
          </p>
        </div>
      </div>
    </div>
  );
}

/** A 6-digit email code field: numeric keyboard on phones, one-time-code autofill, spaces ignored. */
export function CodeInput({ id, value, onChange, invalid }: { id: string; value: string; onChange: (v: string) => void; invalid?: boolean }) {
  return (
    <input
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
      inputMode="numeric"
      autoComplete="one-time-code"
      pattern="[0-9]{6}"
      maxLength={6}
      required
      aria-invalid={invalid}
      placeholder="123456"
      className={`${inputCls} num text-center text-xl tracking-[0.5em]`}
    />
  );
}

/** "Send again" with a visible one-minute wait (the server enforces the same limit). */
export function useCooldown(seconds = 60) {
  const [left, setLeft] = useState(0);
  useEffect(() => {
    if (left <= 0) return;
    const t = setTimeout(() => setLeft((n) => n - 1), 1000);
    return () => clearTimeout(t);
  }, [left]);
  return { left, start: () => setLeft(seconds) };
}

/** Reads a token from the URL fragment (#token=...). Fragments are never sent to servers or written to access logs. */
export function tokenFromHash(): string {
  if (typeof window === "undefined") return "";
  const m = /(?:^|[#&])token=([A-Za-z0-9_-]{20,128})/.exec(window.location.hash);
  return m ? m[1] : "";
}
