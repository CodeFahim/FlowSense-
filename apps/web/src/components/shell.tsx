"use client";

import clsx from "clsx";
import {
  ArrowLeftRight,
  Bot,
  Check,
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  FlaskConical,
  HelpCircle,
  Home,
  Inbox,
  LifeBuoy,
  ListChecks,
  LogOut,
  Menu,
  ScrollText,
  Settings,
  ShieldOff,
  Users,
  Waves,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, Suspense, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, apiGet, apiPost, apiPut, setCsrf } from "@/lib/api";
import { applyAppearance } from "@/lib/appearance";
import { configureFormatting, dateOnly, setDefaultCurrency } from "@/lib/format";
import type { Counts, Currency, Draft, Me, Preferences, Region } from "@/lib/types";
import { AssistantHeaderButton, AssistantLauncher, AssistantProvider } from "./assistant";
import { Converter } from "./converter";
import { AddDialog, AddMenu, type AddKind, type Prefill } from "./records";
import { AnimatePresence, LayoutGroup, fast, motion, spring, useExitValue } from "./motion";
import { ToastProvider } from "./toast";
import { Drawer, IconButton, Loading, Tip } from "./ui";

type Reference = { currencies: Currency[]; regions: Region[] };

const BOOT_TIMEOUT_MS = 12_000; // one session check never waits longer than this
const BOOT_BUDGET_MS = 150_000; // then stop retrying and let the user choose
const SLOW_AFTER_S = 4; // show the explanation once a check takes this long

/** What the user sees while the session is checked: never a silent skeleton for more than a few seconds. */
function SessionCheck({ start, attempt, failed, onRetry }: { start: number; attempt: number; failed: boolean; onRetry: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (failed) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [failed]);
  const seconds = start ? Math.max(0, Math.round((now - start) / 1000)) : 0;
  if (!failed && attempt === 0 && seconds < SLOW_AFTER_S) return <Loading what="Checking your session" />;
  return (
    <div role="status" aria-live="polite" className="mx-auto mt-16 max-w-md rounded-2xl border border-line bg-surface p-6 text-center" data-testid="session-check">
      <p className="font-semibold">{failed ? "FlowSense could not be reached" : "FlowSense is waking up…"}</p>
      <p className="mt-2 text-sm text-muted">
        {failed
          ? "The server did not answer for over two minutes. Your session is not affected. Check your connection, then try again."
          : `The free hosting pauses when nobody has used it for a while and usually starts within a minute. Retrying automatically (${seconds} s${attempt > 0 ? `, attempt ${attempt + 1}` : ""}). You stay signed in.`}
      </p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        <button type="button" onClick={onRetry} className="inline-flex min-h-10 items-center rounded-lg bg-accent px-4 text-sm font-medium text-on-accent hover:bg-accent-hover">{failed ? "Try again" : "Try again now"}</button>
        <Link href="/login" className="inline-flex min-h-10 items-center rounded-lg border border-line px-4 text-sm font-medium hover:bg-sunken">Go to sign-in</Link>
      </div>
    </div>
  );
}
type SessionCtx = {
  me: Me;
  reload: () => Promise<void>;
  setMe: (m: Me) => void;
  ref: Reference;
  counts: Counts;
  refreshCounts: () => void;
  savePrefs: (p: Partial<Preferences> & { complete_onboarding?: boolean; restart_onboarding?: boolean; clear?: string[] }) => Promise<Preferences>;
  openConverter: () => void;
  openAdd: (k: AddKind, prefill?: Prefill) => void;
};
const Ctx = createContext<SessionCtx | null>(null);

export function useSession(): SessionCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useSession outside AppShell");
  return c;
}

type NavItem = { href: string; label: string; Icon: typeof Home; count?: boolean; also?: string[] };
// Four main places. Everything else is one click away under "More".
const NAV: NavItem[] = [
  { href: "/", label: "Dashboard", Icon: Home },
  { href: "/money", label: "Money", Icon: Wallet, also: ["/money-in", "/money-out"] },
  { href: "/customers", label: "Customers", Icon: Users },
  { href: "/assistant", label: "AI Assistant", Icon: Bot },
];
const MORE: NavItem[] = [
  { href: "/approvals", label: "Approvals", Icon: Inbox, count: true },
  { href: "/rescue", label: "Rescue plan", Icon: LifeBuoy },
  { href: "/activity", label: "Activity & receipts", Icon: ScrollText },
  { href: "/setup", label: "Setup checklist", Icon: ListChecks },
  { href: "/settings", label: "Settings", Icon: Settings },
  { href: "/help", label: "Help", Icon: HelpCircle },
];
const DEMO_NAV = { href: "/demo", label: "Demo scenarios", Icon: FlaskConical };
const ZERO: Counts = { approvals: 0, needs_approval: 0, ready_to_send: 0, in_progress: 0 };

function applyPrefs(me: Me, ref?: Reference) {
  const p = me.preferences;
  applyAppearance(p.theme, p.accent);
  configureFormatting({
    currencies: ref?.currencies,
    locale: p.number_locale,
    deviceTime: p.time_display === "device",
    businessTimeZone: me.business.timezone ?? "UTC",
  });
  if (me.business.currency) setDefaultCurrency(me.business.currency);
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<div className="mx-auto max-w-5xl p-6"><Loading what="Loading" /></div>}>
      <Shell>{children}</Shell>
    </Suspense>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const search = useSearchParams();
  const [me, setMeState] = useState<Me | null>(null);
  const [ref, setRef] = useState<Reference | null>(null);
  const [counts, setCounts] = useState<Counts>(ZERO);
  const [drawer, setDrawer] = useState(false);
  const [converter, setConverter] = useState(false);
  // The converter stays rendered while its drawer slides away.
  const converterShown = useExitValue(converter ? true : null);
  const [collapsed, setCollapsed] = useState(false);
  const [add, setAdd] = useState<{ kind: AddKind; prefill?: Prefill } | null>(null);
  // Stable identities: pages call these from effects, which must not re-run (and re-open dialogs) on every render.
  const openAdd = useCallback((kind: AddKind, prefill?: Prefill) => setAdd({ kind, prefill }), []);
  const openConverter = useCallback(() => setConverter(true), []);

  const [waking, setWaking] = useState(0);
  const [bootStart, setBootStart] = useState(0);
  const [bootFailed, setBootFailed] = useState(false);
  const [bootRun, setBootRun] = useState(0); // bump to restart the session check
  const refData = useRef<Reference | undefined>(undefined);

  const setMe = useCallback((m: Me) => {
    setCsrf(m.csrf_token);
    applyPrefs(m, refData.current);
    setMeState(m);
  }, []);

  const reload = useCallback(async () => {
    try {
      setMe(await apiGet<Me>("/api/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
      else throw e;
    }
  }, [router, setMe]);

  const refreshCounts = useCallback(() => {
    apiGet<Counts>("/api/nav/counts").then(setCounts, () => undefined);
  }, []);

  // First load. The free API host sleeps when idle and can take about a minute to answer (or answers 502/503)
  // while it wakes up. Each check is bounded (BOOT_TIMEOUT_MS) and retried with a visible status; after
  // BOOT_BUDGET_MS the page stops and offers "Try again" and "Go to sign-in". Only a real 401 (no valid
  // session) goes to sign-in: a slow or unreachable server never signs anyone out.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    setBootStart(started);
    setBootFailed(false);
    const boot = async (attempt: number) => {
      try {
        const [m, c, r] = await Promise.all([
          apiGet<Me>("/api/auth/me", { timeoutMs: BOOT_TIMEOUT_MS }),
          apiGet<{ currencies: Currency[] }>("/api/reference/currencies", { timeoutMs: BOOT_TIMEOUT_MS }),
          apiGet<{ regions: Region[] }>("/api/reference/regions", { timeoutMs: BOOT_TIMEOUT_MS }),
        ]);
        if (cancelled) return;
        const data = { currencies: c.currencies, regions: r.regions };
        refData.current = data;
        configureFormatting({ currencies: data.currencies });
        setRef(data);
        setMe(m);
        setWaking(0);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) {
          router.replace("/login?signed_out=1");
          return;
        }
        setWaking(attempt);
        if (Date.now() - started < BOOT_BUDGET_MS) timer = setTimeout(() => void boot(attempt + 1), 3000);
        else setBootFailed(true);
      }
    };
    void boot(1);
    try {
      setCollapsed(localStorage.getItem("flowsense.sidebar") === "collapsed");
    } catch {
      // ignore unavailable storage
    }
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [router, setMe, bootRun]);

  useEffect(() => {
    if (me) refreshCounts();
  }, [path, me, refreshCounts]);

  // ?add=invoice on any page opens that form; ?convert=1 opens the converter (used by assistant links).
  useEffect(() => {
    const k = search.get("add") as AddKind | null;
    if (k && ["cash", "invoice", "bill", "payroll", "party", "document"].includes(k)) setAdd({ kind: k });
    if (search.get("convert") === "1") setConverter(true);
  }, [search]);

  // First run: welcome (choose demo or own business) until done. Never repeats once completed.
  useEffect(() => {
    if (me?.needs_onboarding && path !== "/welcome" && !path.startsWith("/setup")) router.replace("/welcome");
  }, [me, path, router]);

  const savePrefs = useCallback(async (p: Partial<Preferences> & { complete_onboarding?: boolean; restart_onboarding?: boolean; clear?: string[] }) => {
    const saved = await apiPut<Preferences>("/api/me/preferences", p);
    const fresh = await apiGet<Me>("/api/auth/me");
    setCsrf(fresh.csrf_token);
    applyPrefs(fresh, refData.current);
    setMeState(fresh);
    return saved;
  }, []);

  if (!me || !ref) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <SessionCheck start={bootStart} attempt={waking} failed={bootFailed} onRetry={() => { setWaking(0); setBootRun((n) => n + 1); }} />
      </div>
    );
  }

  async function logout() {
    try {
      await apiPost("/api/auth/logout");
    } finally {
      // Clear in-memory private data (CSRF token, cached session) before leaving.
      setCsrf("");
      setMeState(null);
      router.replace("/login");
    }
  }

  async function switchTo(id: string) {
    const m = await apiPost<Me>(`/api/workspaces/${id}/switch`);
    setMe(m);
    router.replace(m.needs_onboarding ? "/welcome" : "/");
  }

  function toggleCollapsed() {
    setCollapsed((c) => {
      try {
        localStorage.setItem("flowsense.sidebar", c ? "expanded" : "collapsed");
      } catch {
        // ignore
      }
      return !c;
    });
  }

  function onDraft(d: Draft) {
    const kind: AddKind = d.record === "cash" ? "cash" : d.record;
    setAdd({ kind, prefill: { counterparty: d.fields.counterparty, description: d.fields.description, amount: d.fields.amount, due_date: d.fields.due_date, reference: d.fields.reference } });
  }

  const ctx: SessionCtx = {
    me,
    reload,
    setMe,
    ref,
    counts,
    refreshCounts,
    savePrefs,
    openConverter,
    openAdd,
  };
  const focusMode = path === "/welcome";
  const demoish = me.business.kind === "demo" || me.business.kind === "sample";

  return (
    <Ctx.Provider value={ctx}>
      <ToastProvider>
        <AssistantProvider businessId={me.business.id} businessName={me.business.name}>
          {focusMode ? (
            children
          ) : (
            <div className="flex min-h-dvh">
              <a href="#main" className="sr-only z-50 rounded-lg bg-surface px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2">Skip to content</a>
              <aside className={clsx("sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-line bg-canvas lg:flex", collapsed ? "w-[4.25rem]" : "w-60")}>
                <div className={clsx("flex h-16 items-center", collapsed ? "justify-center" : "px-4")}><Brand compact={collapsed} /></div>
                <Nav id="side" path={path} counts={counts} collapsed={collapsed} demo={demoish} />
                <div className={clsx("border-t border-line p-2", collapsed && "flex justify-center")}>
                  <button type="button" onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"} className="flex min-h-9 items-center gap-2 rounded-lg px-2.5 text-sm text-muted hover:bg-sunken hover:text-ink">
                    {collapsed ? <ChevronsRight className="h-4 w-4" /> : <><ChevronsLeft className="h-4 w-4" /> Collapse</>}
                  </button>
                </div>
              </aside>

              <Drawer open={drawer} onClose={() => setDrawer(false)} side="left" label="Navigation">
                <div className="flex h-16 items-center px-4"><Brand /></div>
                <Nav id="drawer" path={path} counts={counts} demo={demoish} onNavigate={() => setDrawer(false)} />
              </Drawer>

              <div className="flex min-w-0 flex-1 flex-col">
                {me.business.kill_switch && (
                  <div className="flex items-center justify-center gap-2 bg-danger px-4 py-2 text-center text-sm font-medium text-white dark:text-[#1d0b09]" role="alert">
                    <ShieldOff className="h-4 w-4 shrink-0" aria-hidden /> Emergency stop is on. New payments and requests are blocked; payments already in progress still complete.
                  </div>
                )}
                <header className="glass sticky top-0 z-30 flex h-16 items-center gap-2 border-b border-line px-3 sm:px-5">
                  <IconButton label="Open navigation" className="lg:hidden" onClick={() => setDrawer(true)}><Menu className="h-5 w-5" /></IconButton>
                  <div className="lg:hidden"><Brand compact /></div>
                  <div className="ml-auto flex items-center gap-1.5">
                    <SandboxPill me={me} />
                    <button type="button" onClick={() => setConverter(true)} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg px-2.5 text-sm font-medium text-muted hover:bg-sunken hover:text-ink" aria-label="Open currency converter">
                      <ArrowLeftRight className="h-4 w-4" aria-hidden /> <span className="hidden sm:inline">Convert</span>
                    </button>
                    <AssistantHeaderButton />
                    <AddMenu onPick={(k) => setAdd({ kind: k })} />
                    <AccountMenu me={me} onLogout={logout} onSwitch={switchTo} />
                  </div>
                </header>
                <main id="main" className="mx-auto w-full max-w-6xl min-w-0 flex-1 px-4 pt-7 pb-28 sm:px-6 sm:pb-16 lg:px-8">
                  {/* A short entrance on each page change; the key replays it without changing the page tree. */}
                  <motion.div key={path} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={spring}>{children}</motion.div>
                </main>
              </div>

              <Drawer open={converter} onClose={() => setConverter(false)} title="Currency converter">
                {converterShown && <Converter />}
              </Drawer>
            </div>
          )}
          <AddDialog kind={add?.kind ?? null} prefill={add?.prefill} onClose={() => setAdd(null)} />
          <AssistantLauncher hidden={path === "/assistant" || focusMode} onDraft={onDraft} />
        </AssistantProvider>
      </ToastProvider>
    </Ctx.Provider>
  );
}

function Brand({ compact }: { compact?: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2 rounded-lg text-ink" aria-label="FlowSense, go to Home">
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-on-accent"><Waves className="h-[1.15rem] w-[1.15rem]" aria-hidden /></span>
      {!compact && <span className="text-[1.05rem] font-semibold tracking-[-0.01em]">FlowSense</span>}
    </Link>
  );
}

function Nav({ id, path, counts, collapsed, demo, onNavigate }: { id: string; path: string; counts: Counts; collapsed?: boolean; demo: boolean; onNavigate?: () => void }) {
  const queue = [counts.needs_approval ? `${counts.needs_approval} to approve` : "", counts.ready_to_send ? `${counts.ready_to_send} to send` : ""].filter(Boolean).join(", ");
  const item = ({ href, label, Icon, count, also }: NavItem) => {
    const active = href === "/" ? path === "/" : path.startsWith(href) || !!also?.some((a) => path.startsWith(a));
    const n = count ? counts.approvals : 0;
    return (
      <Link
        key={href}
        href={href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        title={collapsed ? label : undefined}
        aria-label={collapsed ? (n ? `${label}: ${queue}` : label) : undefined}
        className={clsx(
          "relative flex min-h-10 items-center gap-3 rounded-lg text-sm",
          collapsed ? "justify-center px-0" : "px-3",
          active ? "font-semibold text-ink" : "text-muted hover:bg-sunken hover:text-ink",
        )}
      >
        {/* The highlight and the accent bar slide from the previous page's item to this one. */}
        {active && (
          <motion.span layoutId="nav-active" className="absolute inset-0 rounded-lg bg-surface shadow-[0_0_0_1px_var(--line)]" transition={spring} aria-hidden>
            <span className="absolute top-2 bottom-2 left-0 w-[3px] rounded-r bg-accent" />
          </motion.span>
        )}
        <Icon className={clsx("relative h-[1.1rem] w-[1.1rem] shrink-0 transition-colors", active && "text-accent")} aria-hidden />
        {!collapsed && <span className="relative flex-1 truncate">{label}</span>}
        {n > 0 && (
          <span className={clsx("num relative rounded-md bg-warn-soft px-1.5 text-xs font-semibold text-warn", collapsed && "absolute top-0.5 right-0.5 px-1 text-[0.65rem]")} aria-hidden={collapsed} title={queue}>
            {n}
            {!collapsed && <span className="sr-only">: {queue}</span>}
          </span>
        )}
      </Link>
    );
  };
  // Urgent places come out of "More" while they need the owner: Approvals with items waiting, Rescue plan
  // while a shortfall case is open. Otherwise the menu stays at its four main places.
  const urgent = MORE.filter((m) => (m.href === "/approvals" && counts.approvals > 0) || (m.href === "/rescue" && !!counts.rescue_open));
  const more = (demo ? [...MORE, DEMO_NAV] : MORE).filter((m) => !urgent.includes(m));
  const inMore = more.some((m) => path.startsWith(m.href));
  return (
    // Separate groups so the sidebar and the phone drawer each have their own sliding highlight.
    <LayoutGroup id={id}>
    <nav aria-label="Main" className={clsx("flex-1 space-y-0.5 overflow-y-auto", collapsed ? "px-2" : "px-3")}>
      {NAV.map(item)}
      {urgent.length > 0 && (
        <div className="space-y-0.5 pt-4" role="group" aria-label="Needs attention">
          {!collapsed && <p className="px-3 pb-1 text-xs font-medium text-warn">Needs attention</p>}
          {urgent.map(item)}
        </div>
      )}
      {collapsed ? (
        <div className="space-y-0.5 pt-5">{more.map(item)}</div>
      ) : (
        <details className="group pt-5" open={inMore || undefined}>
          <summary className="flex min-h-9 cursor-pointer list-none items-center justify-between rounded-lg px-3 text-xs font-medium text-muted hover:bg-sunken hover:text-ink">
            <span className="flex items-center gap-2">
              More
              {counts.approvals > 0 && !urgent.some((u) => u.href === "/approvals") && <span className="num rounded-md bg-warn-soft px-1.5 font-semibold text-warn" title={queue}>{counts.approvals}<span className="sr-only"> waiting for approval</span></span>}
            </span>
            <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" aria-hidden />
          </summary>
          <div className="space-y-0.5 pt-1">{more.map(item)}</div>
        </details>
      )}
    </nav>
    </LayoutGroup>
  );
}

function SandboxPill({ me }: { me: Me }) {
  const b = me.business;
  return (
    <span className="hidden items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs font-medium text-muted md:inline-flex">
      <span className="h-1.5 w-1.5 rounded-full bg-warn" aria-hidden />
      {b.simulated_clock ? `Sandbox · simulated date ${dateOnly(b.sandbox_now, b.timezone ?? "UTC")}` : "Sandbox"}
      <Tip
        label="About the sandbox"
        text={`Synthetic money only: no real banks, payments or messages.${b.simulated_clock ? ` This sample runs on a fixed simulated business clock (${b.timezone}), not today's date.` : ""}${b.currency ? ` Books kept in ${b.currency}.` : ""}`}
      />
    </span>
  );
}

function AccountMenu({ me, onLogout, onSwitch }: { me: Me; onLogout: () => void; onSwitch: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const initials = me.user.display_name.replace(/\(.*\)/, "").trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase() || "?";
  const kindLabel = { workspace: "Your business", demo: "Shared demo", sample: "Private sample", isolation: "Test business" } as const;
  return (
    <div className="relative" ref={box}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="menu" aria-label="Account and workspace menu" className={clsx("flex h-10 items-center gap-2 rounded-lg pr-1 pl-1 hover:bg-sunken", open && "bg-sunken")}>
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent-ink">{initials}</span>
      </button>
      <AnimatePresence>
      {open && (
        <motion.div key="menu" initial={{ opacity: 0, y: -6, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -6, scale: 0.97, transition: fast }} transition={spring} style={{ transformOrigin: "top right" }} role="menu" className="glass glass-strong absolute top-12 right-0 z-40 w-72 rounded-xl border border-line p-1.5 shadow-pop">
          <div className="px-3 py-2.5">
            <p className="truncate text-sm font-semibold">{me.user.display_name}</p>
            <p className="truncate text-xs text-muted">{me.user.email}</p>
            <p className="mt-0.5 text-xs text-muted">Role: <span className="font-medium text-ink">{me.role === "owner" ? "Owner" : "Staff"}</span></p>
          </div>
          <div className="my-1 h-px bg-line" />
          <p className="px-3 pt-1 pb-1 text-xs font-medium text-faint">Workspaces</p>
          {me.workspaces.map((w) => (
            <button key={w.id} role="menuitem" type="button" disabled={w.active} onClick={() => { setOpen(false); onSwitch(w.id); }} className="flex min-h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-sm hover:bg-sunken disabled:cursor-default">
              <span className="min-w-0 flex-1 truncate">{w.name}<span className="block text-xs text-muted">{kindLabel[w.kind]}</span></span>
              {w.active && <Check className="h-4 w-4 text-accent" aria-label="Current workspace" />}
            </button>
          ))}
          <div className="my-1 h-px bg-line" />
          <Link role="menuitem" href="/settings" onClick={() => setOpen(false)} className="flex min-h-9 items-center gap-2 rounded-lg px-3 text-sm hover:bg-sunken"><Settings className="h-4 w-4 text-muted" aria-hidden /> Settings</Link>
          <button role="menuitem" type="button" onClick={onLogout} className="flex min-h-9 w-full items-center gap-2 rounded-lg px-3 text-sm hover:bg-sunken"><LogOut className="h-4 w-4 text-muted" aria-hidden /> Sign out</button>
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  );
}
