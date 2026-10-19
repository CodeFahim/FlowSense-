// Turns backend responses into plain-language notices. The UI never claims
// success unless the backend confirmed it, and keeps these cases distinct:
//   - safety refusal (the kernel said no; nothing happened)
//   - validation error (the request was malformed)
//   - network / server failure (outcome unknown; check status before retrying)
//   - "no change" outcomes from the sandbox (duplicate or not applicable)
//   - pending outcomes (submitted, waiting for the bank)
import { ApiError, type Reason } from "./api";

export type Tone = "ok" | "error" | "info" | "warn";
export type Notice = { tone: Tone; title: string; text?: string; reasons?: Reason[] };

const NO_CHANGE = new Set(["duplicate", "ignored_state", "unmatched", "already_settled", "amount_mismatch", "rejected_overpayment"]);

/** Interprets a sandbox event result ({outcome, detail}) returned by simulator and reconcile routes. */
export function describeEvent(out: { outcome?: string; detail?: string } | null | undefined, okTitle: string): Notice {
  const outcome = out?.outcome;
  if (outcome === "refused") return { tone: "warn", title: okTitle, text: out?.detail };
  if (outcome && NO_CHANGE.has(outcome)) {
    return { tone: "info", title: "No change", text: `${out?.detail ?? ""} Nothing was recorded twice.`.trim() };
  }
  if (outcome === "failed") {
    return { tone: "error", title: "The bank reported the transfer as failed", text: `${out?.detail ?? ""} No money left the account; the reserved funds were released.`.trim() };
  }
  if (outcome === "timeout") {
    return { tone: "warn", title: "Submitted — waiting for bank confirmation", text: out?.detail };
  }
  return { tone: "ok", title: okTitle, text: out?.detail };
}

export function describeError(e: unknown): Notice {
  if (!(e instanceof ApiError)) {
    return {
      tone: "error",
      title: "Could not reach FlowSense",
      text: "The request may or may not have arrived. Check your connection and refresh this page to see the current status before trying again.",
    };
  }
  if (e.code === "SAFETY_DENIED") {
    return { tone: "error", title: "Refused by the safety rules — nothing was paid or sent", reasons: e.reasons };
  }
  if (e.status === 422 || e.code === "VALIDATION_FAILED") {
    return { tone: "error", title: "Please check the input", text: e.message, reasons: e.reasons };
  }
  if (e.status === 401) return { tone: "error", title: "Your session ended", text: "Sign in again, then repeat the step." };
  if (e.status === 403) return { tone: "error", title: "Not allowed", text: e.message };
  if (e.status >= 500) {
    return { tone: "error", title: "Server error — outcome unknown", text: "Refresh to see the current status before retrying. Retrying a submission is safe: it reuses the same idempotency key." };
  }
  return { tone: "error", title: e.message, reasons: e.reasons };
}

/** One-line message for load failures, with a recovery hint when the server is unreachable. */
export function errorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message;
  return `${fallback} FlowSense could not be reached. Check your connection, then refresh. (Free hosting can take about a minute to wake up.)`;
}

/** True when the server definitively answered, so a new attempt should get a fresh idempotency key. */
export function isDefinitive(e: unknown): boolean {
  return e instanceof ApiError && e.status >= 400 && e.status < 500;
}
