"use client";

import clsx from "clsx";
import {
  AlertTriangle,
  Ban,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  CircleDot,
  Clock,
  HelpCircle,
  Info,
  Loader2,
  Send,
  ShieldAlert,
  ShieldCheck,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import type { Reason } from "@/lib/api";
import { AnimatePresence, AnimatedValue, dialogIn, dialogOut, dialogReset, fast, motion, spring, useRise, type DialogMotion } from "./motion";

/* ------------------------------------------------------------------ layout */

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
      <div className="min-w-0 max-w-2xl">
        <h1 className="text-[1.6rem] leading-tight font-semibold tracking-[-0.01em] text-ink">{title}</h1>
        {description && <p className="mt-1.5 text-[0.95rem] leading-relaxed text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
/** @deprecated use PageHeader */
export const PageTitle = ({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) => (
  <PageHeader title={title} description={subtitle} actions={actions} />
);

export function Card({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  return (
    <motion.section id={id} {...useRise()} className={clsx("min-w-0 rounded-xl border border-line bg-surface transition-colors duration-200 hover:border-line-strong", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 px-5 pt-4">
          <div className="min-w-0">
            {title && <h2 className="text-[0.95rem] font-semibold text-ink">{title}</h2>}
            {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className={clsx("px-5 pt-3 pb-5", !(title || actions) && "pt-5", bodyClassName)}>{children}</div>
    </motion.section>
  );
}

/* ------------------------------------------------------------------ buttons */

type Variant = "primary" | "secondary" | "ghost" | "danger" | "quiet";
const variantCls: Record<Variant, string> = {
  primary: "bg-accent text-on-accent hover:bg-accent-hover disabled:opacity-50",
  secondary: "border border-line-strong bg-surface text-ink hover:bg-sunken disabled:opacity-50",
  ghost: "text-accent-ink hover:bg-accent-soft disabled:opacity-50",
  danger: "bg-danger text-white hover:brightness-95 disabled:opacity-50 dark:text-[#1d0b09]",
  quiet: "text-muted hover:bg-sunken hover:text-ink disabled:opacity-50",
};

export type Feedback = "ok" | "error" | null;

/**
 * Brief success/error feedback on the button that ran an action, shown only after the backend answered:
 * `flash("ok")` after a confirmed save, `flash("error")` after a refusal. It clears itself.
 */
export function useFeedback(ms = 1800): [Feedback, (f: Exclude<Feedback, null>) => void] {
  const [f, setF] = useState<Feedback>(null);
  const t = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const flash = useCallback((v: Exclude<Feedback, null>) => {
    setF(v);
    clearTimeout(t.current);
    t.current = setTimeout(() => setF(null), ms);
  }, [ms]);
  useEffect(() => () => clearTimeout(t.current), []);
  return [f, flash];
}

// Motion takes over these handler names; Button never used them.
type MotionConflicts = "onDrag" | "onDragStart" | "onDragEnd" | "onDragOver" | "onDragEnter" | "onDragLeave" | "onDrop" | "onAnimationStart" | "onAnimationEnd" | "onAnimationIteration";

export function Button({
  variant = "primary",
  size = "md",
  busy,
  feedback,
  children,
  className,
  ...rest
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, MotionConflicts> & { variant?: Variant; size?: "sm" | "md"; busy?: boolean; feedback?: Feedback }) {
  const off = rest.disabled || busy;
  return (
    <motion.button
      type="button"
      {...rest}
      data-motion=""
      disabled={off}
      aria-busy={busy || undefined}
      whileHover={off ? undefined : { y: -1 }}
      whileTap={off ? undefined : { scale: 0.96, y: 0 }}
      // A refusal gives a short sideways nudge, a confirmed save a small swell; the icon and page text say the same.
      animate={feedback === "error" ? { x: [0, -5, 5, -3, 0] } : feedback === "ok" ? { scale: [1, 1.04, 1] } : { x: 0, scale: 1 }}
      transition={feedback ? { duration: 0.32 } : spring}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-[color,background-color,border-color,box-shadow,opacity] duration-150 disabled:cursor-not-allowed",
        size === "md" ? "min-h-10 px-3.5 text-sm" : "min-h-8 px-2.5 text-[0.8rem]",
        variantCls[variant],
        feedback === "ok" && "ring-2 ring-ok/60",
        feedback === "error" && "ring-2 ring-danger/60",
        className,
      )}
    >
      <AnimatePresence initial={false} mode="popLayout">
        {busy ? (
          <motion.span key="busy" className="inline-flex" initial={{ opacity: 0, scale: 0.5 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.5, transition: fast }}>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          </motion.span>
        ) : feedback ? (
          <motion.span key={feedback} className={clsx("inline-flex", variant !== "primary" && variant !== "danger" && (feedback === "ok" ? "text-ok" : "text-danger"))} initial={{ opacity: 0, scale: 0.4, rotate: -30 }} animate={{ opacity: 1, scale: 1, rotate: 0 }} exit={{ opacity: 0, scale: 0.5, transition: fast }}>
            {feedback === "ok" ? <Check className="h-4 w-4" aria-hidden /> : <XCircle className="h-4 w-4" aria-hidden />}
          </motion.span>
        ) : null}
      </AnimatePresence>
      {children}
    </motion.button>
  );
}

export function IconButton({ label, children, className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      className={clsx("inline-flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-sunken hover:text-ink", className)}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ badges and status */

export type Tone = "neutral" | "accent" | "ok" | "warn" | "danger" | "info";
const toneCls: Record<Tone, string> = {
  neutral: "bg-sunken text-muted border-line",
  accent: "bg-accent-soft text-accent-ink border-transparent",
  ok: "bg-ok-soft text-ok border-transparent",
  warn: "bg-warn-soft text-warn border-transparent",
  danger: "bg-danger-soft text-danger border-transparent",
  info: "bg-info-soft text-info border-transparent",
};

export function Badge({ tone = "neutral", children, title, icon: Icon }: { tone?: Tone; children: ReactNode; title?: string; icon?: LucideIcon }) {
  return (
    <span title={title} className={clsx("inline-flex max-w-full items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium", toneCls[tone])}>
      {Icon && <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />}
      <span className="truncate">{children}</span>
    </span>
  );
}

// Real states from the backend, in plain words. Colour is never the only signal: each has an icon and text.
const STATUS: Record<string, { text: string; tone: Tone; icon: LucideIcon }> = {
  proposed: { text: "Awaiting approval", tone: "warn", icon: CircleDot },
  approved: { text: "Approved, not sent", tone: "accent", icon: ShieldCheck },
  submitted: { text: "Submitted, pending bank", tone: "info", icon: Clock },
  sent: { text: "Request sent", tone: "info", icon: Send },
  accepted: { text: "Promised, not cash", tone: "info", icon: Clock },
  settled: { text: "Settled", tone: "ok", icon: CheckCircle2 },
  partially_settled: { text: "Partly settled", tone: "warn", icon: CircleDashed },
  denied: { text: "Refused by safety rules", tone: "danger", icon: ShieldAlert },
  failed: { text: "Failed at bank", tone: "danger", icon: XCircle },
  refused: { text: "Refused by customer", tone: "danger", icon: Ban },
  cancelled: { text: "Cancelled", tone: "neutral", icon: X },
  OPEN: { text: "Open", tone: "warn", icon: CircleDot },
  AT_RISK: { text: "At risk", tone: "danger", icon: AlertTriangle },
  AWAITING_ACTION: { text: "Waiting for you", tone: "warn", icon: CircleDot },
  AWAITING_SETTLEMENT: { text: "Waiting for money", tone: "info", icon: Clock },
  RESOLVED: { text: "Resolved", tone: "ok", icon: CheckCircle2 },
  INFEASIBLE: { text: "No safe plan", tone: "danger", icon: XCircle },
  ALLOW: { text: "Allowed", tone: "ok", icon: Check },
  DENY: { text: "Refused", tone: "danger", icon: ShieldAlert },
  NEEDS_APPROVAL: { text: "Needs approval", tone: "warn", icon: CircleDot },
  ok: { text: "OK", tone: "neutral", icon: Check },
};

/** True for backend states that have a defined meaning (used to hide free-form outcome strings). */
export function isKnownStatus(value: string): boolean {
  return value in STATUS && value !== "ok";
}

export function statusText(value: string): string {
  return STATUS[value]?.text ?? value.replace(/_/g, " ").toLowerCase();
}

export function Status({ value }: { value: string }) {
  const s = STATUS[value] ?? { text: value.replace(/_/g, " ").toLowerCase(), tone: "neutral" as Tone, icon: CircleDot };
  return <Badge tone={s.tone} icon={s.icon}>{s.text}</Badge>;
}

/* ------------------------------------------------------------------ notices */

const noticeMap = {
  info: { cls: "border-line bg-surface", Icon: Info, ic: "text-info" },
  warn: { cls: "border-transparent bg-warn-soft", Icon: AlertTriangle, ic: "text-warn" },
  error: { cls: "border-transparent bg-danger-soft", Icon: XCircle, ic: "text-danger" },
  ok: { cls: "border-transparent bg-ok-soft", Icon: CheckCircle2, ic: "text-ok" },
};

export function Notice({ tone, title, children, className, action }: { tone: "info" | "warn" | "error" | "ok"; title?: ReactNode; children?: ReactNode; className?: string; action?: ReactNode }) {
  const m = noticeMap[tone];
  return (
    <motion.div role={tone === "error" ? "alert" : "status"} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={spring} className={clsx("flex gap-3 rounded-xl border px-4 py-3 text-sm", m.cls, className)}>
      <m.Icon className={clsx("mt-0.5 h-4 w-4 shrink-0", m.ic)} aria-hidden />
      <div className="min-w-0 flex-1 space-y-1 text-ink">
        {title && <p className="font-semibold">{title}</p>}
        {children}
      </div>
      {action}
    </motion.div>
  );
}

export function ReasonList({ reasons, tone = "error" }: { reasons: Reason[]; tone?: "error" | "warn" }) {
  if (!reasons.length) return null;
  return (
    <ul className="space-y-1.5">
      {reasons.map((r, i) => (
        <li key={r.code + i} className="flex gap-2 text-sm">
          <ShieldAlert className={clsx("mt-0.5 h-4 w-4 shrink-0", tone === "error" ? "text-danger" : "text-warn")} aria-hidden />
          <span className="min-w-0 break-words">
            {r.message} <code className="ml-0.5 rounded bg-sunken px-1 text-[0.7rem] text-muted">{r.code}</code>
          </span>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------ dialogs and drawers */

/**
 * Drives a native <dialog> (focus trap, Escape, focus return and the top layer stay the browser's) and
 * animates it in and out. Escape goes through `onCancel` so the closing animation plays; the dialog's
 * own close() runs after it, which fires the dialog's onClose exactly as before.
 */
export function useDialog(open: boolean, kind: DialogMotion = "modal", onCancel?: () => void) {
  const ref = useRef<HTMLDialogElement>(null);
  const want = useRef(open);
  const cancelRef = useRef(onCancel);
  useEffect(() => {
    cancelRef.current = onCancel;
  }, [onCancel]);
  useEffect(() => {
    want.current = open;
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      dialogReset(d);
      d.showModal();
      d.querySelector<HTMLElement>("[data-autofocus]")?.focus();
      dialogIn(d, kind);
    } else if (open && d.open) {
      // Reopened while the exit was still playing.
      dialogReset(d);
    } else if (!open && d.open) {
      void dialogOut(d, kind).finally(() => {
        if (want.current || !d.open) return;
        d.close();
        dialogReset(d);
      });
    }
  }, [open, kind]);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    const cancel = (e: Event) => {
      if (!cancelRef.current) return;
      e.preventDefault();
      cancelRef.current();
    };
    d.addEventListener("cancel", cancel);
    return () => d.removeEventListener("cancel", cancel);
  }, []);
  return ref;
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useDialog(open, "modal", onClose);
  const id = useId();
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-labelledby={id}
      className={clsx(
        "m-auto max-h-[92dvh] w-[min(var(--w),calc(100vw-1.5rem))] overflow-hidden rounded-2xl border border-line bg-surface p-0 text-ink shadow-pop",
        wide ? "[--w:44rem]" : "[--w:34rem]",
      )}
    >
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
        <h2 id={id} className="text-base font-semibold">{title}</h2>
        <IconButton label="Close" onClick={onClose} className="-mr-2 h-9 w-9"><X className="h-4 w-4" /></IconButton>
      </div>
      <div className="max-h-[calc(92dvh-3.75rem)] overflow-y-auto px-5 py-4">{children}</div>
    </dialog>
  );
}

export function Drawer({ open, onClose, title, side = "right", children, label }: { open: boolean; onClose: () => void; title?: string; side?: "left" | "right"; children: ReactNode; label?: string }) {
  const ref = useDialog(open, side, onClose);
  const id = useId();
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-labelledby={title ? id : undefined}
      aria-label={title ? undefined : label}
      className={clsx(
        "fixed top-0 m-0 h-dvh max-h-dvh w-[min(24rem,calc(100vw-2.5rem))] max-w-none border-line bg-surface p-0 text-ink shadow-pop",
        side === "left" ? "left-0 border-r" : "right-0 left-auto border-l",
      )}
    >
      <div className="flex h-full flex-col">
        {title && (
          <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
            <h2 id={id} className="text-base font-semibold">{title}</h2>
            <IconButton label="Close" onClick={onClose} className="-mr-2 h-9 w-9"><X className="h-4 w-4" /></IconButton>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </dialog>
  );
}

/* ------------------------------------------------------------------ loading and empty */

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3 py-2">
      <span className="sr-only">{what}…</span>
      <div className="h-6 w-1/3 animate-pulse rounded-md bg-sunken" />
      <div className="h-4 w-2/3 animate-pulse rounded-md bg-sunken" />
      <div className="grid gap-3 sm:grid-cols-3">
        {[0, 1, 2].map((i) => <div key={i} className="h-24 animate-pulse rounded-xl bg-sunken" />)}
      </div>
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <p role="status" className="animate-in flex items-center gap-2 py-3 text-sm text-muted">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}
    </p>
  );
}

export function Empty({ title, children, icon: Icon, action }: { title?: string; children?: ReactNode; icon?: LucideIcon; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-4 py-8 text-center">
      {Icon && <Icon className="mb-2 h-6 w-6 text-faint" aria-hidden />}
      {title && <p className="font-medium text-ink">{title}</p>}
      {children && <p className="mt-1 max-w-sm text-sm text-muted">{children}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ figures */

export function Stat({ label, value, sub, tone, tip }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "danger" | "ok" | "warn"; tip?: string }) {
  return (
    <motion.div {...useRise()} className="min-w-0 rounded-xl border border-line bg-surface px-4 py-3.5 transition-colors duration-200 hover:border-line-strong">
      <p className="flex items-center gap-1 text-sm text-muted">
        {label}
        {tip && <Tip text={tip} />}
      </p>
      <p className={clsx("num mt-1 text-[1.35rem] leading-tight font-semibold tracking-[-0.01em] break-words", tone === "danger" ? "text-danger" : tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-ink")}>
        {/* Figures change only after the backend confirms new data: the old value slides out, the new one in. */}
        {typeof value === "string" ? <AnimatedValue value={value} /> : value}
      </p>
      {sub && <div className="mt-1 text-xs leading-snug text-muted">{sub}</div>}
    </motion.div>
  );
}

/* ------------------------------------------------------------------ tooltips */

export function Tip({ text, label = "More information" }: { text: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        aria-label={label}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-faint hover:text-ink"
      >
        <HelpCircle className="h-3.5 w-3.5" aria-hidden />
      </button>
      {open && (
        <span role="tooltip" id={id} className="animate-in absolute top-6 left-1/2 z-40 w-64 -translate-x-1/2 rounded-lg border border-line bg-surface px-3 py-2 text-xs leading-relaxed font-normal text-ink shadow-pop">
          {text}
        </span>
      )}
    </span>
  );
}

/* ------------------------------------------------------------------ forms */

export const inputCls =
  "w-full min-w-0 rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-faint hover:border-faint focus-visible:border-accent focus-visible:shadow-[0_0_0_3px_var(--accent-soft)] aria-invalid:border-danger disabled:opacity-60";

export function Field({ label, hint, error, htmlFor, children, optional }: { label: string; hint?: ReactNode; error?: string | null; htmlFor: string; children: ReactNode; optional?: boolean }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-medium text-ink">
        {label} {optional && <span className="font-normal text-muted">(optional)</span>}
      </label>
      {children}
      {hint && !error && <p className="text-xs text-muted">{hint}</p>}
      <AnimatePresence initial={false}>
        {error && (
          <motion.p key="error" role="alert" className="text-xs font-medium text-danger" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0, x: [0, -3, 3, 0] }} exit={{ opacity: 0, transition: fast }} transition={{ duration: 0.25 }}>
            {error}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(inputCls, props.className)} />;
}
export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx(inputCls, props.className)} />;
}
export function Select({ children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select {...props} className={clsx(inputCls, "appearance-none pr-9", props.className)}>{children}</select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options, label, nowrap }: { value: T; onChange: (v: T) => void; options: { value: T; label: string; icon?: LucideIcon }[]; label: string; nowrap?: boolean }) {
  const group = useId();
  return (
    <div role="radiogroup" aria-label={label} className={clsx("inline-flex gap-1 rounded-lg border border-line bg-sunken p-1", nowrap ? "flex-nowrap" : "flex-wrap")}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "relative inline-flex min-h-8 shrink-0 items-center gap-1.5 rounded-md px-3 text-sm font-medium whitespace-nowrap",
            value === o.value ? "text-ink" : "text-muted hover:text-ink",
          )}
        >
          {/* The selected pill slides between options. */}
          {value === o.value && <motion.span layoutId={`seg-${group}`} className="absolute inset-0 rounded-md bg-surface shadow-sm ring-1 ring-line" transition={spring} aria-hidden />}
          <span className="relative inline-flex items-center gap-1.5">
            {o.icon && <o.icon className="h-4 w-4" aria-hidden />}
            {o.label}
          </span>
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, tabs, label }: { value: T; onChange: (v: T) => void; tabs: { value: T; label: string; count?: number }[]; label: string }) {
  const group = useId();
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          type="button"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={clsx(
            "relative -mb-px inline-flex min-h-10 shrink-0 items-center gap-1.5 border-b-2 border-transparent px-3 text-sm font-medium whitespace-nowrap",
            value === t.value ? "text-ink" : "text-muted hover:text-ink",
          )}
        >
          {/* The underline slides to the selected tab. */}
          {value === t.value && <motion.span layoutId={`tab-${group}`} className="absolute inset-x-0 -bottom-0.5 h-0.5 rounded-full bg-accent" transition={spring} aria-hidden />}
          {t.label}
          {t.count !== undefined && <span className="num rounded-md bg-sunken px-1.5 text-xs text-muted">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ searchable combobox */

export type ComboItem = { value: string; label: string; hint?: string; keywords?: string };

export function Combobox({
  id,
  items,
  value,
  onChange,
  placeholder,
  suggested,
  emptyText = "No matches",
}: {
  id: string;
  items: ComboItem[];
  value: string | null;
  onChange: (v: string) => void;
  placeholder?: string;
  suggested?: string[];
  emptyText?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);
  const selected = items.find((i) => i.value === value) ?? null;

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let list = needle
      ? items.filter((i) => `${i.value} ${i.label} ${i.keywords ?? ""}`.toLowerCase().includes(needle))
      : items;
    if (needle) {
      // Exact code matches first ("usd" → USD), then label starts-with.
      list = [...list].sort((a, b) => {
        const score = (i: ComboItem) => (i.value.toLowerCase() === needle ? 0 : i.label.toLowerCase().startsWith(needle) ? 1 : 2);
        return score(a) - score(b);
      });
    } else if (suggested?.length) {
      const top = suggested.map((s) => items.find((i) => i.value === s)).filter(Boolean) as ComboItem[];
      list = [...top, ...items.filter((i) => !suggested.includes(i.value))];
    }
    return list.slice(0, 300);
  }, [items, q, suggested]);

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function choose(i: ComboItem) {
    onChange(i.value);
    setQ("");
    setOpen(false);
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter" && open) {
      e.preventDefault();
      if (filtered[active]) choose(filtered[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
      setQ("");
    }
  }

  const listId = `${id}-list`;
  return (
    <div className="relative">
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && filtered[active] ? `${id}-opt-${active}` : undefined}
        autoComplete="off"
        value={open ? q : selected ? `${selected.label}${selected.hint ? ` — ${selected.hint}` : ""}` : ""}
        placeholder={placeholder}
        onFocus={() => {
          setOpen(true);
          setActive(0);
        }}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onKeyDown={onKey}
        className={clsx(inputCls, "pr-9")}
      />
      <ChevronDown className="pointer-events-none absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          className="animate-in absolute z-50 mt-1 max-h-72 w-full overflow-y-auto rounded-xl border border-line bg-surface py-1 shadow-pop"
        >
          {filtered.length === 0 && <li className="px-3 py-2 text-sm text-muted">{emptyText}</li>}
          {filtered.map((i, n) => (
            <li
              key={i.value}
              id={`${id}-opt-${n}`}
              data-index={n}
              role="option"
              aria-selected={i.value === value}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(i)}
              onMouseEnter={() => setActive(n)}
              className={clsx("flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm", n === active && "bg-sunken")}
            >
              <span className="min-w-0 truncate">
                {i.label}
                {i.hint && <span className="ml-1.5 text-muted">{i.hint}</span>}
              </span>
              {suggested?.includes(i.value) && !q && <span className="shrink-0 text-xs text-accent-ink">Suggested</span>}
              {i.value === value && <Check className="h-4 w-4 shrink-0 text-accent" aria-hidden />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ definition rows */

export function Rows({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="divide-y divide-line text-sm">
      {rows.map(([k, v], i) => (
        <div key={i} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-2">
          <dt className="text-muted">{k}</dt>
          <dd className="num min-w-0 text-right break-words text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
