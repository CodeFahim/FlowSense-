"use client";

import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, fast, motion, spring } from "./motion";

type Tone = "ok" | "error" | "info" | "warn";
type Toast = { id: number; tone: Tone; title: string; text?: string };
const Ctx = createContext<(t: Omit<Toast, "id">) => void>(() => undefined);

export function useToast() {
  return useContext(Ctx);
}

const ICON = { ok: CheckCircle2, error: XCircle, info: Info, warn: AlertTriangle };
const COLOR = { ok: "text-ok", error: "text-danger", info: "text-info", warn: "text-warn" };
const BAR = { ok: "bg-ok", error: "bg-danger", info: "bg-info", warn: "bg-warn" };

/** Short confirmations only. Anything the user must read or act on stays inline on the page. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [list, setList] = useState<Toast[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setList((l) => l.filter((x) => x.id !== id)), []);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = next.current++;
    setList((l) => [...l.slice(-2), { ...t, id }]);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      {/* The stack itself never takes clicks; only the toasts do, and they sit above the phone launcher. */}
      <div aria-live="polite" className="pointer-events-none fixed right-3 bottom-20 sm:bottom-4 z-[60] flex w-[min(24rem,calc(100vw-1.5rem))] flex-col gap-2">
        <AnimatePresence initial={false} mode="popLayout">
          {list.map((t) => <ToastCard key={t.id} t={t} onDismiss={dismiss} />)}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}

function ToastCard({ t, onDismiss }: { t: Toast; onDismiss: (id: number) => void }) {
  const Icon = ICON[t.tone];
  const ms = t.tone === "error" ? 9000 : 5000;
  // Hovering or focusing a toast pauses its timer, so it can be read in full.
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const timer = setTimeout(() => onDismiss(t.id), ms);
    return () => clearTimeout(timer);
  }, [paused, ms, onDismiss, t.id]);
  return (
    <motion.div
      layout
      role={t.tone === "error" ? "alert" : "status"}
      initial={{ opacity: 0, y: 16, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, x: 24, transition: fast }}
      transition={spring}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className="pointer-events-auto relative flex gap-3 overflow-hidden rounded-xl border border-line bg-surface px-4 py-3 text-sm shadow-pop"
    >
      <motion.span className={clsx("mt-0.5 inline-flex shrink-0", COLOR[t.tone])} initial={{ scale: 0.4, rotate: -25 }} animate={{ scale: 1, rotate: 0 }} transition={{ ...spring, delay: 0.05 }}>
        <Icon className="h-4 w-4" aria-hidden />
      </motion.span>
      <div className="min-w-0 flex-1">
        <p className="font-medium">{t.title}</p>
        {t.text && <p className="mt-0.5 text-muted">{t.text}</p>}
      </div>
      <button type="button" aria-label="Dismiss" onClick={() => onDismiss(t.id)} className="-mr-1 h-6 w-6 rounded text-muted hover:text-ink">
        <X className="mx-auto h-4 w-4" />
      </button>
      {/* Time left before it closes by itself (it stops while hovered). */}
      {!paused && (
        <motion.span aria-hidden className={clsx("absolute bottom-0 left-0 h-0.5 w-full origin-left opacity-60", BAR[t.tone])} initial={{ scaleX: 1 }} animate={{ scaleX: 0 }} transition={{ duration: ms / 1000, ease: "linear" }} />
      )}
    </motion.div>
  );
}
