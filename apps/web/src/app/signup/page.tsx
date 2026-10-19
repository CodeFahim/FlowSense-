"use client";

import { ArrowLeft, Check } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { AuthLayout, PasswordInput } from "@/components/auth";
import { Button, Field, Input, Notice } from "@/components/ui";
import { ApiError, apiPost, setCsrf } from "@/lib/api";
import type { Me } from "@/lib/types";
import { domainSuggestion, emailProblem, usernameProblem } from "@/lib/validate";

type FieldName = "email" | "full_name" | "username" | "business_type" | "business_name" | "password" | "confirm_password";
type Errors = Partial<Record<FieldName, string>>;

const BUSINESS_TYPES = ["Retail shop", "Restaurant or café", "Clothing or tailoring", "Wholesale or distribution", "Services", "Online shop", "Manufacturing", "Other"];
const STEP2_ORDER: FieldName[] = ["full_name", "username", "business_type", "business_name", "password", "confirm_password"];

export default function SignupPage() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2>(1);
  const [v, setV] = useState<Record<FieldName, string>>({ email: "", full_name: "", username: "", business_type: "", business_name: "", password: "", confirm_password: "" });
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [userCheck, setUserCheck] = useState<{ name: string; ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const focusNext = useRef<FieldName | null>(null);

  const set = (k: FieldName) => (e: { target: { value: string } }) => {
    setV((s) => ({ ...s, [k]: e.target.value }));
    if (errors[k]) setErrors((s) => ({ ...s, [k]: undefined }));
  };

  // Move focus to the field that needs attention after a step change or a server answer.
  useEffect(() => {
    const id = focusNext.current;
    if (id) {
      focusNext.current = null;
      document.getElementById(id)?.focus();
    }
  });

  function continueToDetails(e: FormEvent) {
    e.preventDefault();
    const problem = emailProblem(v.email);
    if (problem) {
      setErrors({ email: problem });
      focusNext.current = "email";
      return;
    }
    setV((s) => ({ ...s, email: s.email.trim() }));
    setErrors({});
    setStep(2);
    focusNext.current = "full_name";
  }

  async function checkUsername() {
    const name = v.username.trim().toLowerCase();
    const local = usernameProblem(name);
    if (local) {
      if (name) setErrors((s) => ({ ...s, username: local }));
      return;
    }
    try {
      const r = await apiPost<{ available: boolean; message: string }>("/api/auth/username-check", { username: name });
      setUserCheck({ name, ok: r.available, text: r.message });
      if (!r.available) setErrors((s) => ({ ...s, username: r.message }));
    } catch {
      setUserCheck(null); // the server checks again on submit
    }
  }

  function localErrors(): Errors {
    const out: Errors = {};
    if (!v.full_name.trim()) out.full_name = "Enter your full name.";
    const u = usernameProblem(v.username);
    if (u) out.username = u;
    if (v.password.length < 12) out.password = "Use at least 12 characters. A short phrase of several words works well.";
    if (!v.confirm_password) out.confirm_password = "Type the password again.";
    else if (v.confirm_password !== v.password) out.confirm_password = "The passwords don't match.";
    return out;
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setFormError(null);
    const local = localErrors();
    if (Object.keys(local).length) {
      setErrors(local);
      focusNext.current = STEP2_ORDER.find((k) => local[k]) ?? null;
      return;
    }
    setBusy(true);
    try {
      const r = await apiPost<({ status: "signed_in" } & Me) | { status: "verify_code"; message: string; email: string }>("/api/auth/register", {
        ...v,
        username: v.username.trim().toLowerCase(),
        business_type: v.business_type.trim() || null,
        business_name: v.business_name.trim() || null,
      });
      if (r.status === "signed_in") {
        setCsrf(r.csrf_token);
        router.replace("/welcome");
      } else {
        // Step 3: the 6-digit code from the email (the account is usable only once the address is confirmed).
        router.replace(`/verify-email?email=${encodeURIComponent(r.email)}`);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = (err.body.fields ?? {}) as Errors;
        if (Object.keys(fields).length) {
          setErrors(fields);
          if (fields.email) {
            setStep(1); // everything typed in step 2 is kept
            focusNext.current = "email";
          } else {
            focusNext.current = STEP2_ORDER.find((k) => fields[k]) ?? null;
          }
        } else {
          setFormError(err.message);
        }
      } else {
        setFormError("FlowSense could not be reached. Check your connection and try again. Free hosting can take about a minute to wake up.");
      }
    } finally {
      setBusy(false);
    }
  }

  const footer = <>Already have an account? <Link href="/login" className="font-medium text-accent-ink hover:underline">Sign in</Link></>;
  const typo = step === 1 ? domainSuggestion(v.email) : null;

  return (
    <AuthLayout
      title={step === 1 ? "Create your account" : "Tell us about you"}
      subtitle={<StepDots step={step} />}
      footer={footer}
    >
      {step === 1 ? (
        <form onSubmit={continueToDetails} className="space-y-4" noValidate>
          <Field label="Email address" htmlFor="email" error={errors.email} hint="Gmail, Outlook or any other email you can open. We use it to sign you in and to send account links.">
            <Input
              id="email" type="email" inputMode="email" autoComplete="email" autoCapitalize="none" spellCheck={false} required
              value={v.email} onChange={set("email")} placeholder="you@example.com"
              aria-invalid={!!errors.email}
            />
          </Field>
          {typo && !errors.email && (
            <p className="text-sm text-muted">
              Did you mean <button type="button" className="font-medium text-accent-ink underline" onClick={() => { setV((s) => ({ ...s, email: typo })); focusNext.current = "email"; }}>{typo}</button>?
            </p>
          )}
          <Button type="submit" className="w-full">Continue</Button>
        </form>
      ) : (
        <form onSubmit={submit} className="space-y-4" noValidate>
          <div className="flex items-center justify-between gap-2 rounded-lg bg-sunken px-3 py-2 text-sm">
            <span className="min-w-0 truncate"><span className="text-muted">Email: </span><span className="font-medium">{v.email}</span></span>
            <button type="button" onClick={() => { setStep(1); focusNext.current = "email"; }} className="flex shrink-0 items-center gap-1 font-medium text-accent-ink hover:underline">
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Change
            </button>
          </div>
          <Field label="Full name" htmlFor="full_name" error={errors.full_name}>
            <Input id="full_name" autoComplete="name" required maxLength={120} value={v.full_name} onChange={set("full_name")} placeholder="e.g. Amina Rahman" aria-invalid={!!errors.full_name} />
          </Field>
          <Field
            label="Username"
            htmlFor="username"
            error={errors.username}
            hint={userCheck && userCheck.ok && userCheck.name === v.username.trim().toLowerCase()
              ? <span className="flex items-center gap-1 text-ok"><Check className="h-3.5 w-3.5" aria-hidden /> Available</span>
              : "3–30 characters: letters, numbers, dots, dashes or underscores. You can sign in with it."}
          >
            <Input
              id="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={30}
              value={v.username} onChange={(e) => { set("username")(e); setUserCheck(null); }} onBlur={checkUsername}
              placeholder="e.g. amina_tailors" aria-invalid={!!errors.username}
            />
          </Field>
          <Field label="Type of business" htmlFor="business_type" optional error={errors.business_type}>
            <Input id="business_type" list="business-types" maxLength={100} value={v.business_type} onChange={set("business_type")} placeholder="e.g. Retail shop" />
            <datalist id="business-types">{BUSINESS_TYPES.map((t) => <option key={t} value={t} />)}</datalist>
          </Field>
          <Field label="Business or shop name" htmlFor="business_name" optional error={errors.business_name} hint="You can add or change this later.">
            <Input id="business_name" autoComplete="organization" maxLength={200} value={v.business_name} onChange={set("business_name")} placeholder="e.g. Rahman Tailors" />
          </Field>
          <Field label="Password" htmlFor="password" error={errors.password} hint="At least 12 characters. A short phrase of several words works well.">
            <PasswordInput id="password" autoComplete="new-password" required maxLength={200} value={v.password} onChange={set("password")} aria-invalid={!!errors.password} />
          </Field>
          <Field label="Confirm password" htmlFor="confirm_password" error={errors.confirm_password}>
            <PasswordInput id="confirm_password" autoComplete="new-password" required maxLength={200} value={v.confirm_password} onChange={set("confirm_password")} aria-invalid={!!errors.confirm_password} />
          </Field>
          {formError && <Notice tone="error">{formError}</Notice>}
          <Button type="submit" busy={busy} className="w-full">Create account</Button>
        </form>
      )}
    </AuthLayout>
  );
}

function StepDots({ step }: { step: 1 | 2 }) {
  return (
    <span className="flex items-center gap-2" aria-label={`Step ${step} of 2`}>
      <span aria-hidden className="flex gap-1">
        <span className="h-1.5 w-6 rounded-full bg-accent" />
        <span className={`h-1.5 w-6 rounded-full ${step === 2 ? "bg-accent" : "bg-line-strong"}`} />
      </span>
      <span>Step {step} of 2 · {step === 1 ? "your email" : "your details"}</span>
    </span>
  );
}
