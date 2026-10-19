"use client";

import { Camera, CheckCircle2, FileUp, Loader2, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { Badge, Button, Notice } from "./ui";
import { AnimatePresence, fast, itemMotion, motion, spring } from "./motion";
import { ApiError, apiPost, apiUpload } from "@/lib/api";

export type SuggestedField = { value: string; confidence: "high" | "medium" | "low"; source: "ai" | "rules" | "both"; label: string; note?: string; ref?: string };
export type Suggestion = {
  batch_id: string;
  purpose: "business" | "customer";
  fields: Record<string, SuggestedField>;
  missing: string[];
  missing_for_invoice: string[];
  notes: string[];
  flags: { tone: "info" | "warn" | "error"; text: string; field?: string; code?: string }[];
  documents: { id: string; name: string; kind: string; method: string; chars: number; instruction_like: boolean }[];
  ai_used: boolean;
  doc_type?: "invoice" | "receipt" | "business_licence" | "customer_document" | "unknown";
  doc_type_label?: string;
};

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.docx,.xlsx,application/pdf,image/png,image/jpeg";
const MAX_FILES = 10;
const MAX_MB = 5;
const EXT = /\.(pdf|png|jpe?g|docx|xlsx)$/i;

/**
 * Pick one or more files (or take photos), upload them one by one, then ask FlowSense to read them.
 * Nothing is saved to your records here: the parent shows the suggestions for review.
 */
// Stable keys for chosen files (each File object keeps its number), so removing one animates that
// row instead of re-keying the rows after it.
const fileIds = new WeakMap<File, number>();
let lastFileId = 0;
function keyOf(f: File): number {
  let id = fileIds.get(f);
  if (id === undefined) {
    id = ++lastFileId;
    fileIds.set(f, id);
  }
  return id;
}

export function DocReader({ purpose, onRead, hint }: { purpose: "business" | "customer"; onRead: (s: Suggestion) => void; hint?: string }) {
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  // Upload progress of the current file (0–1), or null while the server reads the files (no real measure).
  const [progress, setProgress] = useState<number | null>(null);
  const [over, setOver] = useState(false);
  const [readCount, setReadCount] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const pick = useRef<HTMLInputElement>(null);
  const cam = useRef<HTMLInputElement>(null);

  function add(list: FileList | null, dropped = false) {
    if (!list) return;
    setErr(null);
    const next = [...files];
    setReadCount(0);
    for (const f of Array.from(list)) {
      // The file picker already filters by type; dropped files are checked here.
      if (dropped && !EXT.test(f.name)) {
        setErr(`${f.name} isn't a PDF, photo (PNG or JPG), Word or Excel file.`);
        continue;
      }
      if (next.length >= MAX_FILES) {
        setErr(`Up to ${MAX_FILES} files at a time.`);
        break;
      }
      if (f.size > MAX_MB * 1024 * 1024) {
        setErr(`${f.name} is larger than ${MAX_MB} MB. Use a smaller photo or a PDF.`);
        continue;
      }
      next.push(f);
    }
    setFiles(next);
  }

  async function read() {
    if (!files.length || busy) return;
    setErr(null);
    setNotes([]);
    let batch: string | null = null;
    const found: string[] = [];
    try {
      for (const [i, f] of files.entries()) {
        setBusy(`Uploading ${i + 1} of ${files.length}: ${f.name}`);
        setProgress(0);
        const fd = new FormData();
        fd.append("file", f);
        fd.append("purpose", purpose);
        if (batch) fd.append("batch_id", batch);
        const r = await apiUpload<{ batch_id: string; note: string | null; duplicate_file: string | null }>("/api/documents", fd, setProgress);
        batch = r.batch_id;
        if (r.note) found.push(`${f.name}: ${r.note}`);
      }
      setProgress(null);
      setBusy(files.some((f) => f.type.startsWith("image/")) ? "Reading the text in your photos…" : "Reading your documents…");
      const s = await apiPost<Suggestion>(`/api/documents/batches/${batch}/suggest`);
      setNotes(found);
      setReadCount(files.length);
      setFiles([]);
      onRead(s);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "The upload didn't finish. Check your connection and try again.");
      setNotes(found);
    } finally {
      setBusy(null);
      setProgress(null);
    }
  }

  function onDrag(e: DragEvent<HTMLDivElement>, entering: boolean) {
    if (busy || !Array.from(e.dataTransfer.types).includes("Files")) return;
    e.preventDefault();
    // Leaving into a child element is not leaving the zone.
    if (!entering && e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setOver(entering);
  }
  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setOver(false);
    if (!busy) add(e.dataTransfer.files, true);
  }

  return (
    <div className="space-y-3">
      <div
        className="dropzone rounded-xl border-2 border-dashed border-line-strong p-4 text-center"
        data-over={over}
        onDragEnter={(e) => onDrag(e, true)}
        onDragOver={(e) => onDrag(e, true)}
        onDragLeave={(e) => onDrag(e, false)}
        onDrop={onDrop}
      >
        <motion.span className="mx-auto inline-flex" animate={over ? { y: -4, scale: 1.15 } : { y: 0, scale: 1 }} transition={spring} aria-hidden>
          <FileUp className="h-6 w-6 text-accent" />
        </motion.span>
        <p className="mt-2 text-sm font-medium">{over ? "Drop to add these files" : "Add a PDF, photo, Word or Excel file"}</p>
        <p className="mt-0.5 text-xs text-muted">{hint ?? `Up to ${MAX_FILES} files, ${MAX_MB} MB each. Several photos of one document are read together.`}</p>
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          <Button variant="secondary" onClick={() => pick.current?.click()} disabled={!!busy}><FileUp className="h-4 w-4" aria-hidden /> Choose files</Button>
          <Button variant="secondary" onClick={() => cam.current?.click()} disabled={!!busy} className="sm:hidden"><Camera className="h-4 w-4" aria-hidden /> Take a photo</Button>
        </div>
        <input ref={pick} type="file" multiple accept={ACCEPT} className="sr-only" tabIndex={-1} aria-hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        <input ref={cam} type="file" accept="image/*" capture="environment" className="sr-only" tabIndex={-1} aria-hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </div>
      {files.length > 0 && (
        <ul className="space-y-1" aria-label="Files to read">
          <AnimatePresence initial={false}>
            {files.map((f) => (
              <motion.li key={keyOf(f)} {...itemMotion} className="flex items-center justify-between gap-2 rounded-lg bg-sunken px-3 py-1.5 text-sm">
                <span className="min-w-0 truncate">{f.name} <span className="text-xs text-muted">({Math.max(1, Math.round(f.size / 1024))} KB)</span></span>
                <button type="button" aria-label={`Remove ${f.name}`} disabled={!!busy} onClick={() => setFiles(files.filter((x) => x !== f))} className="flex h-7 w-7 items-center justify-center rounded-md text-muted hover:bg-surface hover:text-ink"><X className="h-4 w-4" aria-hidden /></button>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
      <AnimatePresence initial={false} mode="wait">
        {busy ? (
          <motion.div key="busy" className="space-y-1.5" role="status" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, transition: fast }} transition={spring}>
            <p className="flex items-center gap-2 text-sm text-muted">
              <Loader2 className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden />
              <span className="min-w-0 break-words">{busy}{progress !== null && <span className="num"> · {Math.round(progress * 100)}%</span>}</span>
            </p>
            {progress !== null ? (
              // Real bytes sent, reported by the browser.
              <div className="h-1.5 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-label="Upload progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
                <motion.div className="h-full origin-left bg-accent" initial={{ scaleX: 0 }} animate={{ scaleX: progress }} transition={{ type: "spring", stiffness: 200, damping: 30 }} />
              </div>
            ) : (
              // The server is reading the files: no measurable progress, so an indeterminate bar (never a fake percentage).
              <div className="relative h-1.5 overflow-hidden rounded-full bg-sunken" aria-hidden>
                <span className="indeterminate absolute inset-y-0 w-1/3 rounded-full bg-accent/70" />
              </div>
            )}
          </motion.div>
        ) : !err && readCount > 0 ? (
          <motion.p key="done" className="flex items-center gap-2 text-sm font-medium text-ok" role="status" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, transition: fast }} transition={spring}>
            <motion.span className="inline-flex" initial={{ scale: 0.3, rotate: -40 }} animate={{ scale: 1, rotate: 0 }} transition={{ ...spring, delay: 0.05 }}><CheckCircle2 className="h-4 w-4" aria-hidden /></motion.span>
            Read {readCount === 1 ? "1 file" : `${readCount} files`}. Check every suggested value before saving.
          </motion.p>
        ) : null}
      </AnimatePresence>
      {err && <Notice tone="error">{err}</Notice>}
      {notes.map((n) => <Notice key={n} tone="warn">{n}</Notice>)}
      {files.length > 0 && <Button onClick={read} busy={!!busy} className="w-full">{err ? "Try again" : `Read ${files.length === 1 ? "this file" : `these ${files.length} files`}`}</Button>}
      <p className="text-xs text-muted">Nothing is saved until you check the details and press Save. Uploads stay private to this business. Text inside documents is never treated as instructions.</p>
    </div>
  );
}

/**
 * A small label next to a pre-filled value: how sure FlowSense is, where in the document it was found
 * (file, page and line) and, when given, which record field it will be saved to.
 */
export function FieldSource({ f, dest }: { f?: SuggestedField; dest?: string }) {
  if (!f) return null;
  const sure = f.confidence === "high" ? "Read from document" : f.confidence === "medium" ? "Read from document: check it" : "Uncertain: please check";
  return (
    <span className="mt-1 flex flex-wrap items-center gap-1.5 text-xs" data-testid="field-source">
      <Badge tone={f.confidence === "low" ? "warn" : "info"}>{sure}</Badge>
      {f.source !== "rules" && <span className="text-muted">{f.source === "both" ? "AI and text rules agree" : "Read by AI"}</span>}
      {dest && <span className="text-muted">→ saved to {dest}</span>}
      {f.note && <span className="text-warn">{f.note}</span>}
      {f.ref && <span className="block w-full break-words text-muted">Found in {f.ref}</span>}
    </span>
  );
}

/** "Detected: Invoice" (or a warning when the document type isn't recognised). */
export function DocTypeBadge({ s }: { s: Suggestion }) {
  if (!s.doc_type_label) return null;
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted">Detected document:</span>
      <Badge tone={s.doc_type === "unknown" ? "warn" : "accent"}>{s.doc_type_label}</Badge>
      {s.doc_type === "unknown" && <span className="text-xs text-muted">Check every value, or type the details yourself.</span>}
    </p>
  );
}

export function SuggestionNotes({ s }: { s: Suggestion }) {
  return (
    <div className="space-y-2">
      {s.flags.map((f) => <Notice key={f.text} tone={f.tone === "error" ? "error" : f.tone === "warn" ? "warn" : "info"}>{f.text}</Notice>)}
      {s.notes.map((n) => <Notice key={n} tone="warn">{n}</Notice>)}
      {s.missing.length > 0 && <Notice tone="warn">Not found in the document: {s.missing.join(", ")}. Please type it.</Notice>}
      <p className="text-xs text-muted">Read from {s.documents.length} file{s.documents.length === 1 ? "" : "s"}{s.ai_used ? " with the AI reader and text rules" : " with text rules"}. Check every value before saving.</p>
    </div>
  );
}
