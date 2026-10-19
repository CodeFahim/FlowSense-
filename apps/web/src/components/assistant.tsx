"use client";

import clsx from "clsx";
import { ArrowDown, ArrowRight, ArrowUp, Check, CheckCircle2, FileSignature, ListChecks, MessageSquare, Minus, Pencil, Plus, RotateCcw, Search, ShieldAlert, ShieldCheck, Sparkles, Square, Trash2, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Badge, Button, ReasonList, Tip, useDialog } from "./ui";
import { AnimatePresence, fast, motion, spring } from "./motion";
import { ApprovalDialog } from "./approval-dialog";
import { useSession } from "./shell";
import { ApiError, apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import { recordsChanged } from "./records";
import type { Action, AssessedOption, AssistantStatus, ChatMessage, ChatReply, Dashboard, Draft, OptionAssessment, Proposal, Setup } from "@/lib/types";

/* ------------------------------------------------------------------ the bot mark (original artwork) */

export type BotState = "idle" | "thinking" | "responding";

export function BotMark({ className, state = "idle" }: { className?: string; state?: BotState }) {
  // A rounded speech bubble with two eyes and a small lifebuoy antenna: FlowSense's own glyph.
  return (
    <svg viewBox="0 0 32 32" className={clsx(className, state === "thinking" && "bot-thinking", state === "responding" && "bot-responding")} aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <g className="bot-antenna">
        <circle cx="16" cy="4.5" r="2" />
        <path d="M16 6.5v3" />
      </g>
      <path d="M7 9.5h18a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3h-9l-5 4v-4H7a3 3 0 0 1-3-3v-9a3 3 0 0 1 3-3Z" />
      <circle className="bot-eye" cx="12" cy="17" r="1.6" fill="currentColor" stroke="none" />
      <circle className="bot-eye" cx="20" cy="17" r="1.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

/* ------------------------------------------------------------------ state shared across pages */

type Failure = { text: string; category?: string; retry: string; canDemo: boolean };
export type ChatSummary = { id: string; title: string; updated_at: string; created_at: string; messages: number };

type AssistantCtx = {
  messages: ChatMessage[];
  status: AssistantStatus | null;
  busy: boolean;
  /** What the icon shows: thinking only while a request is in flight; responding briefly while a new answer appears. */
  phase: BotState;
  chats: ChatSummary[];
  chatId: string | null;
  newChat: () => Promise<void>;
  openChat: (id: string) => Promise<void>;
  renameChat: (id: string, title: string) => Promise<void>;
  deleteChat: (id: string) => Promise<void>;
  failure: Failure | null;
  open: boolean;
  setOpen: (o: boolean) => void;
  send: (text: string, opts?: { useDemo?: boolean }) => Promise<void>;
  /** Open the assistant and ask: used by the quick-action buttons on every page. */
  ask: (text: string) => void;
  /** Runs the user's decision on one suggestion and returns its new state. */
  decide: (p: Proposal, choice: "confirm" | "dismiss", opts?: { refresh?: boolean }) => Promise<Proposal>;
  /** Reloads the conversation and tells every page that records changed. */
  refresh: () => Promise<void>;
  cancel: () => void;
  clear: () => Promise<void>;
  businessName: string;
};
const Ctx = createContext<AssistantCtx | null>(null);

export function useAssistant(): AssistantCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAssistant outside AssistantProvider");
  return c;
}

/**
 * One conversation per signed-in user and workspace, kept on the server (not in browser storage).
 * Navigating between pages keeps it; switching workspace or signing out reloads it from scratch.
 */
export function AssistantProvider({ businessId, businessName, children }: { businessId: string; businessName: string; children: ReactNode }) {
  const path = usePathname();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [open, setOpenState] = useState(false);
  const [phase, setPhase] = useState<BotState>("idle");
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const chatRef = useRef<string | null>(null);
  useEffect(() => {
    chatRef.current = chatId;
  }, [chatId]);
  const opener = useRef<HTMLElement | null>(null);
  const abort = useRef<AbortController | null>(null);
  // Focus returns to whatever opened the assistant (launcher, header button, Help) when it closes.
  const setOpen = useCallback((o: boolean) => {
    if (o) opener.current = document.activeElement as HTMLElement | null;
    setOpenState(o);
    // After the closing animation, once the page is interactive again.
    if (!o) setTimeout(() => opener.current?.isConnected && opener.current.focus(), 260);
  }, []);
  // The idle "breathing" of the launcher pauses while the tab is hidden (see .bot-breathe in globals.css).
  useEffect(() => {
    const sync = () => {
      document.documentElement.dataset.pageHidden = document.hidden ? "true" : "false";
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);
  const nextId = useRef(-1);

  const refreshChats = useCallback(() => apiGet<{ chats: ChatSummary[] }>("/api/assistant/chats").then((r) => setChats(r.chats), () => undefined), []);
  const loadChat = useCallback(async (id: string | null) => {
    const r = await apiGet<{ chat: ChatSummary | null; messages: ChatMessage[] }>(`/api/assistant/conversation${id ? `?chat_id=${encodeURIComponent(id)}` : ""}`);
    setMessages(r.messages);
    setChatId(r.chat?.id ?? null);
    setFailure(null);
  }, []);

  useEffect(() => {
    setMessages([]);
    setFailure(null);
    setChatId(null);
    void loadChat(null).catch(() => undefined);
    void refreshChats();
    apiGet<AssistantStatus>("/api/assistant/status").then(setStatus, () => undefined);
  }, [businessId, loadChat, refreshChats]);

  const newChat = useCallback(async () => {
    const c = await apiPost<ChatSummary>("/api/assistant/chats");
    setChatId(c.id);
    setMessages([]);
    setFailure(null);
    void refreshChats();
  }, [refreshChats]);
  const openChat = useCallback(async (id: string) => {
    await loadChat(id);
  }, [loadChat]);
  const renameChat = useCallback(async (id: string, title: string) => {
    await apiPatch(`/api/assistant/chats/${id}`, { title });
    await refreshChats();
  }, [refreshChats]);
  const deleteChat = useCallback(async (id: string) => {
    await apiDelete(`/api/assistant/chats/${id}`);
    await refreshChats();
    if (chatRef.current === id) await loadChat(null).catch(() => setMessages([]));
  }, [loadChat, refreshChats]);

  const send = useCallback(
    async (text: string, opts?: { useDemo?: boolean }) => {
      const t = text.trim();
      if (!t || busy) return;
      setFailure(null);
      setBusy(true);
      setPhase("thinking");
      setMessages((m) => [...m, { id: nextId.current--, role: "user", text: t, created_at: new Date().toISOString() }]);
      const ctl = new AbortController();
      abort.current = ctl;
      try {
        const reply = await apiPost<ChatReply & { chat?: ChatSummary }>("/api/assistant/chat", { message: t, page: path, use_demo: !!opts?.useDemo, chat_id: chatRef.current }, {}, ctl.signal);
        setMessages((m) => [...m, { ...reply, id: nextId.current--, role: "assistant", created_at: new Date().toISOString() }]);
        if (reply.chat) setChatId(reply.chat.id);
        void refreshChats();
        // A short "responding" cue while the new answer appears; never while nothing is happening.
        setPhase("responding");
        setTimeout(() => setPhase((p) => (p === "responding" ? "idle" : p)), 900);
      } catch (e) {
        if (ctl.signal.aborted) {
          setFailure({ text: "Stopped. Nothing was changed.", retry: t, canDemo: false });
        } else if (e instanceof ApiError) {
          const cat = (e.body as { category?: string }).category;
          setFailure({ text: e.message, category: cat, retry: t, canDemo: e.code === "AI_UNAVAILABLE" });
        } else {
          setFailure({ text: "The assistant could not be reached. Check your connection and try again.", retry: t, canDemo: false });
        }
      } finally {
        abort.current = null;
        setBusy(false);
        setPhase((p) => (p === "thinking" ? "idle" : p));
      }
    },
    [busy, path, refreshChats],
  );

  const ask = useCallback(
    (text: string) => {
      setOpen(true);
      void send(text);
    },
    [send, setOpen],
  );

  // The user's explicit decision on one suggestion. The server re-validates, runs it, verifies the
  // result and appends a "Done and checked" note; then every page reloads its figures.
  const decide = useCallback(async (p: Proposal, choice: "confirm" | "dismiss", opts?: { refresh?: boolean }) => {
    const mark = (next: Proposal) =>
      setMessages((ms) => ms.map((m) => (m.proposals?.some((x) => x.id === p.id) ? { ...m, proposals: m.proposals.map((x) => (x.id === p.id ? next : x)) } : m)));
    let next: Proposal;
    try {
      next = await apiPost<Proposal>(`/api/assistant/proposals/${p.id}/${choice}`);
    } catch (e) {
      next = { ...p, status: "failed", result: { verified: false, message: e instanceof ApiError ? e.message : "FlowSense could not be reached. Nothing was changed." } };
    }
    mark(next);
    // "Confirm all" refreshes once at the end instead of after every suggestion.
    if (opts?.refresh !== false) {
      const r = await apiGet<{ messages: ChatMessage[] }>("/api/assistant/conversation").catch(() => null);
      if (r) setMessages(r.messages);
      if (choice === "confirm") recordsChanged();
    }
    return next;
  }, []);

  const refresh = useCallback(async () => {
    const r = await apiGet<{ messages: ChatMessage[] }>("/api/assistant/conversation").catch(() => null);
    if (r) setMessages(r.messages);
    recordsChanged();
  }, []);

  const cancel = useCallback(() => abort.current?.abort(), []);
  const clear = useCallback(async () => {
    await apiDelete("/api/assistant/conversation");
    setMessages([]);
    setFailure(null);
    setChatId(null);
    setChats([]);
  }, []);

  return <Ctx.Provider value={{ messages, status, busy, phase, chats, chatId, newChat, openChat, renameChat, deleteChat, failure, open, setOpen, send, ask, decide, refresh, cancel, clear, businessName }}>{children}</Ctx.Provider>;
}

/* ------------------------------------------------------------------ safe text rendering */

// Only **bold** and "- " bullet lines are interpreted. Everything is rendered as React text nodes:
// no HTML, no Markdown links, no images, so model or document text can't inject markup or URLs.
function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return <>{parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : <span key={i}>{p}</span>))}</>;
}

export function SafeText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={`l${blocks.length}`} className="list-disc space-y-1 pl-5">{list.map((l, i) => <li key={i} className="break-words"><Inline text={l} /></li>)}</ul>);
      list = [];
    }
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const bullet = /^\s*[-•*]\s+(.*)$/.exec(line);
    if (bullet) {
      list.push(bullet[1]);
      continue;
    }
    flush();
    if (line.trim()) blocks.push(<p key={`p${blocks.length}`} className="break-words"><Inline text={line.replace(/^#+\s*/, "")} /></p>);
  }
  flush();
  return <div className="space-y-2">{blocks}</div>;
}

const internal = (href: string) => href.startsWith("/") && !href.startsWith("//");

/* ------------------------------------------------------------------ the conversation UI */

export function AssistantPanel({ compact, onNavigate, onDraft }: { compact?: boolean; onNavigate?: () => void; onDraft: (d: Draft) => void }) {
  const a = useAssistant();
  const path = usePathname();
  const [input, setInput] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);
  // Only the message list scrolls. It is moved with scrollTop, never scrollIntoView (which also scrolls the
  // dialog and the page, pushing the header and composer out of view).
  const listRef = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const seen = useRef(0);
  const [unread, setUnread] = useState(false);
  const toBottom = useCallback((smooth = false) => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    nearBottom.current = true;
    setUnread(false);
  }, []);
  const onListScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom.current) setUnread(false);
  }, []);
  // First render and reopening: start at the newest message.
  useLayoutEffect(() => {
    toBottom();
  }, [toBottom]);
  useLayoutEffect(() => {
    const count = a.messages.length;
    const added = count > seen.current;
    const mine = added && a.messages[count - 1]?.role === "user";
    seen.current = count;
    // Follow new content only when the reader is already at the bottom (or just sent something);
    // otherwise keep their place and offer a "New messages" button.
    if (nearBottom.current || mine) toBottom();
    else if (added) setUnread(true);
  }, [a.messages, a.busy, a.failure, toBottom]);
  // The list can change height (window resize, on-screen keyboard): stay at the bottom if the reader was there.
  useEffect(() => {
    const el = listRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (nearBottom.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useLayoutEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
    // The composer only scrolls once it reaches its maximum height; the message list stays the one scroller.
    ta.style.overflowY = ta.scrollHeight > 160 ? "auto" : "hidden";
  }, [input]);

  function submit(text: string) {
    if (!text.trim()) return;
    setInput("");
    void a.send(text).then(() => taRef.current?.focus());
  }
  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit(input);
    }
  }

  const mode = a.status?.mode ?? "demo";
  const tone = mode === "live" ? "accent" : mode === "off" ? "danger" : "warn";
  const pageLabel = PAGE_NAMES[path] ?? "this page";

  return (
    <div className={clsx("flex min-h-0 flex-1 flex-col overflow-hidden", compact ? "h-full" : "")}>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 text-xs text-muted">
        <ChatsMenu />
        <Badge tone={tone}>{a.status?.label ?? "Assistant"}</Badge>
        {mode === "demo" && <Tip label="About the demo assistant" text="This server runs the offline demo assistant: a fixed set of questions answered from FlowSense's own figures. It is not a language model." />}
        <span className="min-w-0 truncate">Using: {a.businessName || "your business"} · {pageLabel}</span>
        <span className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="quiet" onClick={() => void a.newChat()} disabled={a.busy || a.messages.length === 0} aria-label="Start a new chat"><Plus className="h-4 w-4" aria-hidden /> New chat</Button>
        </span>
      </div>

      <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={listRef} onScroll={onListScroll} data-testid="chat-messages" className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4" aria-live="polite" aria-busy={a.busy}>
        {a.messages.length === 0 && !a.busy ? (
          <Starter onAsk={submit} compact={compact} />
        ) : (
          <ol className="space-y-5">
            {a.messages.map((m, idx) => (
              <motion.li
                key={m.id}
                // Only messages arriving now animate (they have temporary negative ids); history loads still.
                initial={m.id < 0 ? { opacity: 0, y: 10, x: m.role === "user" ? 12 : -8 } : false}
                animate={{ opacity: 1, y: 0, x: 0 }}
                transition={spring}
                className={clsx("flex gap-2.5", m.role === "user" && "justify-end")}
              >
                {m.role === "assistant" && <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-ink"><BotMark className="h-4 w-4" /></span>}
                <div className={clsx("min-w-0", m.role === "user" ? "max-w-[85%] rounded-2xl rounded-br-md bg-sunken px-3.5 py-2" : "flex-1")}>
                  <span className="sr-only">{m.role === "user" ? "You said:" : "Assistant:"}</span>
                  {m.role === "user" ? <p className="text-sm whitespace-pre-wrap break-words">{m.text}</p> : <Answer m={m} latest={idx === a.messages.length - 1} onNavigate={onNavigate} onDraft={onDraft} onAsk={submit} />}
                </div>
              </motion.li>
            ))}
          </ol>
        )}
        <AnimatePresence initial={false}>
        {a.busy && (
          <motion.div key="busy" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, transition: fast }} transition={spring} className="mt-4 flex items-center gap-2 text-sm text-muted" role="status">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-soft text-accent-ink"><BotMark className="h-4 w-4" state="thinking" /></span>
            Checking your records
            <span className="thinking-dots inline-flex gap-0.5" aria-hidden><span /><span /><span /></span>
            <Button size="sm" variant="secondary" onClick={a.cancel} className="ml-auto"><Square className="h-3.5 w-3.5" aria-hidden /> Stop</Button>
          </motion.div>
        )}
        </AnimatePresence>
        {a.failure && (
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0, x: [0, -4, 4, 0] }} transition={{ duration: 0.3 }} className="mt-4 rounded-xl bg-danger-soft px-4 py-3 text-sm" role="alert">
            <p className="font-medium">{a.failure.category ? "AI unavailable" : "No answer"}</p>
            <p className="mt-0.5">{a.failure.text}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" onClick={() => a.send(a.failure!.retry)}><RotateCcw className="h-3.5 w-3.5" aria-hidden /> Try again</Button>
              {a.failure.canDemo && <Button size="sm" variant="quiet" onClick={() => a.send(a.failure!.retry, { useDemo: true })}>Ask the offline demo assistant instead</Button>}
            </div>
          </motion.div>
        )}
      </div>
      <AnimatePresence>
        {unread && (
          <motion.button
            key="unread"
            type="button"
            onClick={() => toBottom(true)}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8, transition: fast }}
            transition={spring}
            className="absolute bottom-3 left-1/2 inline-flex min-h-9 -translate-x-1/2 items-center gap-1.5 rounded-full bg-accent px-3.5 text-xs font-medium text-on-accent shadow-pop hover:bg-accent-hover"
          >
            <ArrowDown className="h-3.5 w-3.5" aria-hidden /> New messages
          </motion.button>
        )}
      </AnimatePresence>
      </div>

      <form onSubmit={(e) => { e.preventDefault(); submit(input); }} className="border-t border-line p-3">
        {a.messages.length > 0 && <QuickActions onAsk={submit} disabled={a.busy} small />}
        <div className="flex items-end gap-2 rounded-xl border border-line-strong bg-surface p-1.5 focus-within:border-accent">
          <label htmlFor={compact ? "chat-drawer" : "chat-page"} className="sr-only">Message the assistant</label>
          <textarea
            id={compact ? "chat-drawer" : "chat-page"}
            ref={taRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            maxLength={2000}
            placeholder="Ask about cash, bills, invoices or what to do next"
            className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-sm placeholder:text-faint focus-visible:outline-none"
          />
          <Button type="submit" busy={a.busy} disabled={!input.trim()} aria-label="Send message" className="h-10 w-10 shrink-0 rounded-lg px-0">
            {!a.busy && <ArrowUp className="h-4 w-4" aria-hidden />}
          </Button>
        </div>
        <p className="mt-1.5 px-1 text-[0.7rem] text-muted">
          Enter to send. Nothing happens until you press Confirm; the assistant can&apos;t approve, pay or send money. <Tip label="About chat history" text={`History is kept for ${a.status?.retention_days ?? 30} days, for your account in this workspace only.`} />
        </p>
      </form>
    </div>
  );
}

const PAGE_NAMES: Record<string, string> = {
  "/": "Dashboard",
  "/money": "Money",
  "/money/forecast": "Sales forecast",
  "/customers": "Customers",
  "/money-in": "Money in",
  "/money-out": "Money out",
  "/rescue": "Rescue plan",
  "/approvals": "Approvals",
  "/activity": "Activity",
  "/settings": "Settings",
  "/setup": "Setup",
  "/assistant": "Assistant",
};

export const QUICK_ACTIONS: { text: string; hint: string; Icon: typeof Search }[] = [
  { text: "Analyse my business", hint: "Cash, money owed, bills due and the biggest risk", Icon: Search },
  { text: "Explain my cash shortage", hint: "How much, why, and by when", Icon: ShieldAlert },
  { text: "Help me plan payments", hint: "What to pay first and how to cover it", Icon: ListChecks },
];

/** The three main things to ask. Shown in the assistant, on Home and in the empty states. */
export function QuickActions({ onAsk, disabled, small }: { onAsk: (t: string) => void; disabled?: boolean; small?: boolean }) {
  if (small) {
    return (
      <div className="mb-2 flex flex-wrap gap-1.5" role="group" aria-label="Quick questions">
        {QUICK_ACTIONS.map(({ text, Icon }) => (
          <button key={text} type="button" disabled={disabled} onClick={() => onAsk(text)} className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-xs font-medium hover:bg-sunken disabled:opacity-50">
            <Icon className="h-3.5 w-3.5 text-accent" aria-hidden /> {text}
          </button>
        ))}
      </div>
    );
  }
  return (
    <div className="grid gap-2" role="group" aria-label="Quick questions">
      {QUICK_ACTIONS.map(({ text, hint, Icon }) => (
        <button key={text} type="button" disabled={disabled} onClick={() => onAsk(text)} className="lift group flex min-h-14 items-center gap-3 rounded-xl border border-line bg-surface px-3.5 py-2.5 text-left hover:border-accent hover:bg-accent-soft disabled:opacity-50">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-ink"><Icon className="h-4 w-4" aria-hidden /></span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">{text}</span>
            <span className="block text-xs text-muted">{hint}</span>
          </span>
          <ArrowRight className="h-4 w-4 shrink-0 text-faint transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-ink" aria-hidden />
        </button>
      ))}
    </div>
  );
}

function Starter({ onAsk, compact }: { onAsk: (t: string) => void; compact?: boolean }) {
  const a = useAssistant();
  const s = a.status?.suggestions;
  return (
    <div className={compact ? "" : "pt-4"}>
      <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-soft text-accent-ink"><BotMark className="h-6 w-6" /></span>
      <h2 className="mt-3 text-lg font-semibold">Hi! What would you like to know?</h2>
      <p className="mt-1 text-sm text-muted">I already have your cash, invoices, bills and deadlines, so you don&apos;t need to type them. Pick one to start, or ask in your own words.</p>
      <Checked />
      <div className="mt-4"><QuickActions onAsk={onAsk} /></div>
      <p className="mt-5 text-xs font-medium tracking-wide text-muted uppercase">Other questions</p>
      <div className={clsx("mt-2 grid gap-2", !compact && "sm:grid-cols-2")}>
        {(s?.primary ?? []).map((q) => (
          <button key={q} type="button" onClick={() => onAsk(q)} className="group flex min-h-11 items-center justify-between gap-3 rounded-xl border border-line bg-surface px-3.5 py-2.5 text-left text-sm hover:border-line-strong hover:bg-sunken">
            {q} <ArrowRight className="h-4 w-4 shrink-0 text-faint transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-ink" aria-hidden />
          </button>
        ))}
      </div>
      {s && s.more.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer font-medium text-muted hover:text-ink">More examples</summary>
          <div className="mt-2 flex flex-wrap gap-2">
            {s.more.map((q) => <button key={q} type="button" onClick={() => onAsk(q)} className="max-w-full rounded-lg border border-line px-3 py-1.5 text-left text-xs hover:bg-sunken">{q}</button>)}
          </div>
        </details>
      )}
    </div>
  );
}

function ProposalCard({ p, onNavigate }: { p: Proposal; onNavigate?: () => void }) {
  const a = useAssistant();
  const [busy, setBusy] = useState<"confirm" | "dismiss" | null>(null);
  const expired = p.status === "pending" && !!p.expires_at && new Date(p.expires_at) <= new Date();
  const status = expired ? "expired" : p.status;
  async function go(choice: "confirm" | "dismiss") {
    setBusy(choice);
    await a.decide(p, choice);
    setBusy(null);
  }
  return (
    <div className={clsx("rounded-xl border p-3", status === "pending" ? "border-accent bg-accent-soft/40" : "border-line bg-surface")}>
      <p className="flex items-start gap-2 font-medium">
        <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden />
        <span className="min-w-0 break-words">{p.summary}</span>
      </p>
      <p className="mt-1 text-xs text-muted">{p.effect}</p>
      {status === "pending" ? (
        <div className="mt-2.5 flex flex-wrap gap-2">
          <Button size="sm" busy={busy === "confirm"} disabled={!!busy} onClick={() => go("confirm")}>Confirm</Button>
          <Button size="sm" variant="secondary" busy={busy === "dismiss"} disabled={!!busy} onClick={() => go("dismiss")}>Dismiss</Button>
        </div>
      ) : (
        <motion.p initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={spring} className={clsx("mt-2 flex items-start gap-1.5 text-xs font-medium", status === "done" && p.result?.verified ? "text-ok" : status === "cancelled" ? "text-muted" : "text-danger")} role="status">
          {status === "done" && <span className="animate-pop mt-px inline-flex shrink-0"><CheckCircle2 className="h-3.5 w-3.5" aria-hidden /></span>}
          <span>
            {status === "done" ? (p.result?.verified ? "Done and checked. " : "Done, but not confirmed. ") + (p.result?.message ?? "")
              : status === "cancelled" ? "Dismissed. Nothing was changed."
              : status === "expired" ? "Expired. Ask again so the assistant uses your latest records."
              : `Not done: ${p.result?.message ?? "something went wrong"}. Nothing was changed.`}
            {status === "done" && p.result?.href && internal(p.result.href) && <> <Link href={p.result.href} onClick={onNavigate} className="underline">Open</Link></>}
          </span>
        </motion.p>
      )}
      {status === "done" && p.result?.record === "payment_request" && !p.result.blocked && p.result.id && <ApproveFromChat actionId={p.result.id} onNavigate={onNavigate} />}
    </div>
  );
}

/**
 * The owner can approve a confirmed payment request right here: the same dialog as in Approvals (fresh safety
 * check, the backend decides). Approving does not send it; sending stays a separate step in Approvals.
 */
function ApproveFromChat({ actionId, onNavigate }: { actionId: string; onNavigate?: () => void }) {
  const { me } = useSession();
  const a = useAssistant();
  const [action, setAction] = useState<Action | null>(null);
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<"idle" | "loading" | "approved" | "elsewhere" | "error">("loading");
  const owner = me.role === "owner";
  // The request's real status (it may have been approved here, in Approvals, or by someone else meanwhile).
  useEffect(() => {
    if (!owner) return;
    let live = true;
    apiGet<Action>(`/api/actions/${actionId}`).then(
      (act) => live && (setAction(act), setState(act.status === "proposed" ? "idle" : act.status === "approved" ? "approved" : "elsewhere")),
      () => live && setState("idle"),
    );
    return () => {
      live = false;
    };
  }, [actionId, owner]);
  if (!owner) return <p className="mt-2 text-xs text-muted">The owner approves it in Approvals before anything is sent.</p>;
  async function load() {
    setState("loading");
    try {
      const act = await apiGet<Action>(`/api/actions/${actionId}`);
      setAction(act);
      if (act.status === "proposed") {
        setOpen(true);
        setState("idle");
      } else {
        setState(act.status === "approved" ? "approved" : "elsewhere");
      }
    } catch {
      setState("error");
    }
  }
  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-2 border-t border-line pt-2.5">
      {state === "approved" ? (
        <p className="flex items-center gap-1.5 text-xs font-medium text-ok" role="status">
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Approved, not sent yet.{" "}
          <Link href="/approvals" onClick={onNavigate} className="underline">Send it from Approvals</Link>
        </p>
      ) : state === "elsewhere" ? (
        <p className="text-xs text-muted">This request was already handled. <Link href="/approvals" onClick={onNavigate} className="underline">See Approvals</Link></p>
      ) : (
        <>
          <Button size="sm" busy={state === "loading"} onClick={() => void load()}>
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> Review safety check and approve
          </Button>
          <span className="text-xs text-muted">Approving doesn&apos;t send it.</span>
          {state === "error" && <span className="text-xs text-danger" role="alert">Couldn&apos;t load the request. Try again or open Approvals.</span>}
        </>
      )}
      {action && open && (
        <ApprovalDialog
          action={action}
          open={open}
          onClose={() => setOpen(false)}
          onDone={() => {
            setState("approved");
            void a.refresh();
          }}
        />
      )}
    </div>
  );
}

/** One click for several suggestions from the same answer, after the user has seen each one listed above. */
function ConfirmAll({ all }: { all: Proposal[] }) {
  const a = useAssistant();
  const [busy, setBusy] = useState(false);
  const pending = all.filter((p) => p.status === "pending" && !(p.expires_at && new Date(p.expires_at) <= new Date()));
  // The result is read from the suggestions themselves (they are reloaded from the server after confirming).
  const done = all.filter((p) => p.status === "done" && p.result?.verified !== false && !p.result?.blocked).length;
  const dismissed = all.filter((p) => p.status === "cancelled").length;
  if (pending.length >= 2 || busy) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-dashed border-accent/60 p-2.5">
        <Button
          size="sm"
          busy={busy}
          onClick={async () => {
            setBusy(true);
            for (const p of pending) await a.decide(p, "confirm", { refresh: false });
            await a.refresh();
            setBusy(false);
          }}
        >
          <ListChecks className="h-3.5 w-3.5" aria-hidden /> Confirm all {pending.length}
        </Button>
        <span className="text-xs text-muted">Saves every suggestion listed above, one after another. Nothing is paid or sent.</span>
      </div>
    );
  }
  if (pending.length > 0 || done === 0) return null;
  const allDone = done === all.length;
  return (
    <motion.p initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={spring} role="status" className={clsx("flex items-center gap-1.5 text-xs font-medium", allDone ? "text-ok" : "text-warn")}>
      {allDone ? <CheckCircle2 className="h-4 w-4" aria-hidden /> : <ShieldAlert className="h-4 w-4" aria-hidden />}
      {allDone ? `All ${done} done and checked.` : `${done} of ${all.length} done${dismissed ? `, ${dismissed} dismissed` : ""}. See each suggestion above for what happened.`}
    </motion.p>
  );
}

const OPTION_TONE = { feasible: "ok", conditional: "warn", prohibited: "danger", insufficient_information: "neutral" } as const;
const OPTION_ICON = { feasible: CheckCircle2, conditional: ListChecks, prohibited: ShieldAlert, insufficient_information: Search } as const;
const SITUATION: [string, string][] = [
  ["spendable_cash", "Spendable cash"],
  ["protected_reserve", "Protected reserve"],
  ["gap", "Gap"],
  ["deadline", "Deadline"],
];

function OptionRow({ o, recommended }: { o: AssessedOption; recommended: boolean }) {
  const details = [
    ...(o.rule ? [`Rule: ${o.rule}`] : []),
    ...(o.conditions ?? []).map((c) => `Needs: ${c}`),
    ...(o.requirements ?? []).map((c) => `Then: ${c}`),
    ...(o.missing ?? []).map((c) => `Missing: ${c}`),
    ...(o.timing ? [o.timing] : []),
    ...(o.basis ? [o.basis] : []),
  ];
  return (
    <li className="rounded-lg border border-line bg-surface p-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={OPTION_TONE[o.class]} icon={OPTION_ICON[o.class]}>{o.label}</Badge>
        {recommended && <Badge tone="accent">Recommended next step</Badge>}
        {o.cash_impact && o.class !== "prohibited" && <span className="text-xs text-muted">covers up to {o.cash_impact.display}</span>}
      </div>
      <p className="mt-1 font-medium break-words">{o.title}</p>
      {o.class === "conditional" && o.conditions_met === false && <p className="text-xs text-muted">Not agreed yet. It counts only after the conditions below are met and recorded.</p>}
      {details.length > 0 && (
        <details className="mt-1 text-xs text-muted">
          <summary className="cursor-pointer">Why and what it depends on</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">{details.map((d) => <li key={d} className="break-words">{d}</li>)}</ul>
        </details>
      )}
    </li>
  );
}

/** Options as classified by FlowSense's backend: the same classes the AI text must use. */
function OptionCards({ a }: { a: OptionAssessment }) {
  if (a.status === "no_shortfall") return null;
  const sit = a.situation ?? {};
  return (
    <section aria-label="Options checked against your rules" className="space-y-2 rounded-xl border border-line bg-sunken/60 p-3">
      <p className="text-xs font-medium text-muted">
        Checked by FlowSense against your rules{a.hypothetical ? ` · what-if: ${a.hypothetical.description} ${a.hypothetical.amount.display} (not saved)` : ""}
      </p>
      {a.status === "insufficient_information" ? (
        <div className="text-sm">
          <Badge tone="neutral" icon={Search}>Not enough information</Badge>
          <ul className="mt-1 list-disc pl-4 text-sm">{(a.missing ?? []).map((x) => <li key={x}>{x}</li>)}</ul>
          {a.note && <p className="mt-1 text-xs text-muted">{a.note}</p>}
        </div>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:grid-cols-4">
            {SITUATION.filter(([k]) => sit[k]).map(([k, label]) => (
              <div key={k} className="min-w-0">
                <dt className="text-muted">{label}</dt>
                <dd className="font-medium break-words">{typeof sit[k] === "string" ? (sit[k] as string) : (sit[k] as { display: string }).display}</dd>
              </div>
            ))}
          </dl>
          <ul className="space-y-1.5">{a.options.map((o) => <OptionRow key={o.id} o={o} recommended={a.recommended?.id === o.id} />)}</ul>
          {a.ruled_out.length > 0 && (
            <details className="text-xs text-muted">
              <summary className="cursor-pointer">Ruled out by your records ({a.ruled_out.length})</summary>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">{a.ruled_out.map((r) => <li key={r.title} className="break-words"><span className="font-medium">{r.title}:</span> {r.reason}</li>)}</ul>
            </details>
          )}
          {a.calculation && (
            <details className="text-xs text-muted">
              <summary className="cursor-pointer">How the gap is calculated</summary>
              <p className="mt-1">{a.calculation}</p>
            </details>
          )}
        </>
      )}
    </section>
  );
}

/** Splits an answer into the short part shown first and the "Assumptions" detail shown on request. */
function splitDetail(text: string): [string, string] {
  const i = text.search(/\n\s*\*\*(Assumptions|Evidence|How this was calculated)[^*]*:\*\*/);
  return i > 0 ? [text.slice(0, i).trimEnd(), text.slice(i).trim()] : [text, ""];
}

function Answer({ m, latest, onNavigate, onDraft, onAsk }: { m: ChatMessage; latest: boolean; onNavigate?: () => void; onDraft: (d: Draft) => void; onAsk: (t: string) => void }) {
  // Few, relevant actions: at most three page links, and follow-up questions only under the latest answer.
  const links = (m.links ?? []).filter((l) => internal(l.href)).slice(0, 3);
  const [main, detail] = splitDetail(m.text);
  const cites = (m.citations ?? []).filter((c) => c.label);
  if (m.mode === "system") {
    return (
      <div className="flex items-start gap-2 rounded-xl bg-ok-soft px-3 py-2 text-sm" role="status">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-ok" aria-hidden />
        <SafeText text={m.text} />
      </div>
    );
  }
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={m.mode === "live" ? "accent" : m.mode === "quick" ? "info" : "warn"}>{m.label ?? (m.mode === "live" ? "Live AI" : "Offline demo assistant")}</Badge>
        {m.activity && <span className="text-xs text-muted">{m.activity}</span>}
      </div>
      {m.notice && <p className="text-xs text-muted">{m.notice}</p>}
      <SafeText text={main} />
      {m.options && <OptionCards a={m.options} />}
      {m.decision && "reasons" in m.decision && m.decision.reasons.length > 0 && <ReasonList reasons={m.decision.reasons} />}
      {m.proposed_policy && (
        <div className="rounded-xl border border-line bg-surface p-3 text-sm">
          <p className="font-medium">Proposed policy v{m.proposed_policy.version}: not active yet</p>
          <p className="text-xs text-muted">The owner reviews and activates it in Approvals.</p>
        </div>
      )}
      {(m.proposals ?? []).map((p) => <ProposalCard key={p.id} p={p} onNavigate={onNavigate} />)}
      {(m.proposals ?? []).length > 1 && <ConfirmAll all={m.proposals ?? []} />}
      {(m.drafts ?? []).map((d, i) => (
        <div key={i} className="flex flex-wrap items-start gap-3 rounded-xl border border-line bg-surface p-3">
          <FileSignature className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="font-medium">Draft {d.record}: not saved</p>
            <p className="text-xs break-words text-muted">{Object.entries(d.fields).map(([k, v]) => `${k.replace("_", " ")}: ${v}`).join(" · ")}</p>
          </div>
          <Button size="sm" onClick={() => onDraft(d)}>Review and save</Button>
        </div>
      ))}
      {links.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {links.map((l) => (
            <Link key={l.href} href={l.href} onClick={onNavigate} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line bg-surface px-3 text-sm font-medium hover:bg-sunken">
              {l.label} <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
          ))}
        </div>
      )}
      {latest && (m.suggestions ?? []).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {m.suggestions!.slice(0, 3).map((q) => <button key={q} type="button" onClick={() => onAsk(q)} className="rounded-lg border border-line px-3 py-1.5 text-left text-xs hover:bg-sunken">{q}</button>)}
        </div>
      )}
      {(detail || cites.length > 0) && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">{detail ? "Assumptions and sources" : `Sources (${cites.length})`}</summary>
          {detail && <div className="mt-1.5 text-sm text-ink"><SafeText text={detail} /></div>}
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {cites.map((c) => (
              <li key={`${c.kind}-${c.ref}`}>
                {c.href && internal(c.href) ? <Link href={c.href} onClick={onNavigate} className="rounded-md bg-sunken px-2 py-0.5 hover:underline">{c.label}</Link> : <span className="rounded-md bg-sunken px-2 py-0.5">{c.label}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {m.based_on_records_at && <p className="text-[0.7rem] text-faint">Based on your records at {new Date(m.based_on_records_at).toLocaleTimeString()}. Ask again after changes for fresh figures.</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ launcher + drawer / mobile sheet */

/** True while `open`, and for `ms` after it turns false (so closing content can animate out). */
function useLinger(open: boolean, ms: number) {
  const [linger, setLinger] = useState(open);
  if (open && !linger) setLinger(true);
  useEffect(() => {
    if (open) return;
    const t = setTimeout(() => setLinger(false), ms);
    return () => clearTimeout(t);
  }, [open, ms]);
  return open || linger;
}

/** Header button (tablet and desktop): always visible, never covers page content. */
export function AssistantHeaderButton() {
  const a = useAssistant();
  return (
    <motion.button
      type="button"
      data-motion=""
      onClick={() => a.setOpen(true)}
      aria-label="Open AI assistant"
      whileHover={{ y: -1 }}
      whileTap={{ scale: 0.94 }}
      transition={spring}
      className={clsx(
        "bot-press relative hidden min-h-10 items-center gap-1.5 rounded-lg bg-accent px-3 text-sm font-medium text-on-accent hover:bg-accent-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:inline-flex",
        a.phase === "idle" && !a.open && "bot-breathe",
        a.phase === "thinking" && "bot-glow-think",
        a.phase === "responding" && "bot-glow-reply",
      )}
    >
      <BotMark className={clsx("h-5 w-5", a.phase === "idle" && "bot-idle")} state={a.phase} /> Ask AI
      <BusyDot show={a.busy} className="-top-0.5 -right-0.5" />
    </motion.button>
  );
}

function BusyDot({ show, className }: { show: boolean; className: string }) {
  return (
    <AnimatePresence>
      {show && <motion.span key="dot" className={clsx("absolute h-2.5 w-2.5 rounded-full bg-warn", className)} initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0, transition: fast }} aria-hidden />}
    </AnimatePresence>
  );
}

export function AssistantLauncher({ onDraft, hidden }: { onDraft: (d: Draft) => void; hidden?: boolean }) {
  const a = useAssistant();
  const ref = useDialog(a.open, "sheet", () => a.setOpen(false));
  // Keep the conversation on screen while the panel slides away, then unmount it.
  const shown = useLinger(a.open, 260);
  // The page behind the open assistant doesn't scroll (wheel/touch never chain to it); restored on close.
  useEffect(() => {
    if (!a.open) return;
    const root = document.documentElement;
    const prev = root.style.overflow;
    root.style.overflow = "hidden";
    return () => {
      root.style.overflow = prev;
    };
  }, [a.open]);
  if (hidden) return null;
  return (
    <>
      {/* Always rendered (the modal sheet makes it inert while open) so focus can return to it. */}
      {(
        <motion.button
          type="button"
          data-motion=""
          onClick={() => a.setOpen(true)}
          aria-label="Open AI assistant"
          title="AI assistant"
          initial={{ scale: 0.6, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          whileTap={{ scale: 0.88 }}
          transition={spring}
          className={clsx(
            "bot-press fixed right-4 bottom-4 z-30 flex h-14 w-14 items-center justify-center sm:hidden rounded-full bg-accent text-on-accent shadow-pop hover:bg-accent-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent print:hidden",
            a.phase === "idle" && !a.open && "bot-breathe",
            a.phase === "thinking" && "bot-glow-think",
            a.phase === "responding" && "bot-glow-reply",
          )}
        >
          <BotMark className={clsx("h-7 w-7", a.phase === "idle" && "bot-idle")} state={a.phase} />
          <BusyDot show={a.busy} className="top-1 right-1" />
        </motion.button>
      )}
      <dialog
        ref={ref}
        onClose={() => a.setOpen(false)}
        onClick={(e) => e.target === ref.current && a.setOpen(false)}
        aria-label="AI assistant"
        className={clsx(
          // overflow-hidden: the message list is the only scrolling area (no second scrollbar on the dialog).
          "m-0 max-h-none max-w-none overflow-hidden border-line bg-surface p-0 text-ink shadow-pop",
          // Phone: bottom sheet. Larger screens: right-hand panel.
          "fixed inset-x-0 top-auto bottom-0 h-[88dvh] w-full rounded-t-2xl border-t",
          "sm:inset-x-auto sm:top-0 sm:right-0 sm:left-auto sm:h-dvh sm:w-[min(28rem,100vw)] sm:rounded-none sm:border-t-0 sm:border-l",
        )}
      >
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
            <h2 className="flex items-center gap-2 text-base font-semibold"><BotMark className="h-5 w-5 text-accent" state={a.phase} /> AI assistant</h2>
            <span className="sr-only" aria-live="polite">{a.phase === "thinking" ? "The assistant is checking your records" : ""}</span>
            <div className="flex items-center gap-1">
              <Link href="/assistant" onClick={() => a.setOpen(false)} className="rounded-lg px-2 py-1.5 text-xs font-medium text-muted hover:bg-sunken hover:text-ink">Full page</Link>
              <button type="button" onClick={() => a.setOpen(false)} aria-label="Minimize assistant" className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-sunken hover:text-ink"><Minus className="h-4 w-4" aria-hidden /></button>
              <button type="button" onClick={() => a.setOpen(false)} aria-label="Close assistant" className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-sunken hover:text-ink"><X className="h-4 w-4" aria-hidden /></button>
            </div>
          </div>
          {shown && <AssistantPanel compact onNavigate={() => a.setOpen(false)} onDraft={(d) => { a.setOpen(false); onDraft(d); }} />}
        </div>
      </dialog>
    </>
  );
}

/* ------------------------------------------------------------------ chat history (from the bot's profile icon) */

function ChatsMenu() {
  const a = useAssistant();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "That didn't work. Try again.");
  return (
    <div className="relative" ref={box} onKeyDown={(e) => { if (e.key === "Escape" && open) { e.stopPropagation(); setOpen(false); } }}>
      <button
        type="button"
        onClick={() => { setOpen((o) => !o); setErr(null); }}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label="Your chats"
        title="Your chats"
        className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-soft text-accent-ink hover:ring-2 hover:ring-accent/40"
      >
        <BotMark className="h-5 w-5" state={a.phase} />
      </button>
      <AnimatePresence>
      {open && (
        <motion.div key="chats" initial={{ opacity: 0, y: -6, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -6, scale: 0.97, transition: fast }} transition={spring} style={{ transformOrigin: "top left" }} className="absolute top-10 left-0 z-20 w-[min(20rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-2 text-sm text-ink shadow-pop">
          <div className="flex items-center justify-between px-2 py-1">
            <p className="font-semibold">Your chats</p>
            <Button size="sm" variant="secondary" disabled={a.busy} onClick={() => { void a.newChat().then(() => setOpen(false), fail); }}><Plus className="h-3.5 w-3.5" aria-hidden /> New chat</Button>
          </div>
          {err && <p className="px-2 py-1 text-xs text-danger" role="alert">{err}</p>}
          {a.chats.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted">No saved chats yet. Ask a question and it appears here.</p>
          ) : (
            <ul className="max-h-72 overflow-y-auto">
              {a.chats.map((c) => (
                <li key={c.id} className={clsx("group flex items-center gap-1 rounded-lg px-1", c.id === a.chatId && "bg-sunken")}>
                  {editing === c.id ? (
                    <form className="flex flex-1 items-center gap-1 py-1" onSubmit={(e) => { e.preventDefault(); void a.renameChat(c.id, title).then(() => setEditing(null), fail); }}>
                      <label htmlFor={`rename-${c.id}`} className="sr-only">Chat name</label>
                      <input id={`rename-${c.id}`} autoFocus value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} className="min-h-8 min-w-0 flex-1 rounded-md border border-line-strong bg-surface px-2 text-sm" />
                      <IconBtn label="Save name" type="submit"><Check className="h-4 w-4" aria-hidden /></IconBtn>
                      <IconBtn label="Cancel renaming" onClick={() => setEditing(null)}><X className="h-4 w-4" aria-hidden /></IconBtn>
                    </form>
                  ) : confirmDel === c.id ? (
                    <div className="flex flex-1 flex-wrap items-center gap-1 py-1">
                      <span className="min-w-0 flex-1 truncate text-xs">Delete &ldquo;{c.title}&rdquo;?</span>
                      <Button size="sm" variant="danger" onClick={() => { setConfirmDel(null); void a.deleteChat(c.id).catch(fail); }}>Delete</Button>
                      <Button size="sm" variant="quiet" onClick={() => setConfirmDel(null)}>Keep</Button>
                    </div>
                  ) : (
                    <>
                      <button type="button" onClick={() => { void a.openChat(c.id).then(() => setOpen(false), fail); }} className="flex min-h-10 min-w-0 flex-1 items-center gap-2 px-1 text-left">
                        <MessageSquare className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate">{c.title}</span>
                          <span className="block text-[0.7rem] text-muted">{new Date(c.updated_at).toLocaleString()} · {c.messages} message{c.messages === 1 ? "" : "s"}</span>
                        </span>
                      </button>
                      <IconBtn label={`Rename ${c.title}`} onClick={() => { setEditing(c.id); setTitle(c.title); }}><Pencil className="h-3.5 w-3.5" aria-hidden /></IconBtn>
                      <IconBtn label={`Delete ${c.title}`} onClick={() => setConfirmDel(c.id)}><Trash2 className="h-3.5 w-3.5" aria-hidden /></IconBtn>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
          <p className="px-2 pt-2 text-[0.7rem] text-muted">Only you can see these chats, and only in this workspace.</p>
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  );
}

function IconBtn({ label, children, onClick, type = "button" }: { label: string; children: ReactNode; onClick?: () => void; type?: "button" | "submit" }) {
  return (
    <button type={type} onClick={onClick} aria-label={label} title={label} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted hover:bg-sunken hover:text-ink">
      {children}
    </button>
  );
}

/** "I checked your records": the same deterministic checks as the Dashboard, shown when the assistant opens. */
function Checked() {
  const [d, setD] = useState<Dashboard | { setup_required: true; setup: Setup } | null>(null);
  useEffect(() => {
    apiGet<Dashboard | { setup_required: true; setup: Setup }>("/api/dashboard").then(setD, () => setD(null));
  }, []);
  if (!d) return null;
  if ("setup_required" in d && d.setup_required) {
    const next = d.setup.steps.find((x) => !x.done);
    const done = d.setup.steps.filter((x) => x.done).length;
    return (
      <div className="mt-4 rounded-xl bg-sunken p-3 text-sm" role="status">
        <p className="font-medium">I checked your setup: {done} of {d.setup.steps.length} steps done.</p>
        {next && <p className="mt-1 text-muted">Next: <Link href={next.href} className="font-medium text-accent-ink underline">{next.title}</Link>. I&apos;ll only ask for what&apos;s missing.</p>}
      </div>
    );
  }
  const dash = d as Dashboard;
  const tasks = dash.home.tasks.slice(0, 3);
  return (
    <div className="mt-4 rounded-xl bg-sunken p-3 text-sm" role="status">
      <p className="font-medium">I checked your records: {dash.home.status.sentence}</p>
      {tasks.length > 0 && (
        <ul className="mt-2 space-y-1">
          {tasks.map((t) => <li key={t.key}><Link href={t.href} className="text-accent-ink underline">{t.title}</Link></li>)}
        </ul>
      )}
    </div>
  );
}
