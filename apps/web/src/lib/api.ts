// Thin client for the FlowSense API. Same-origin; the session lives in an
// HttpOnly cookie the browser sends automatically. The CSRF token is kept in
// memory only (never localStorage) and sent on every mutation.

export type Reason = { code: string; message: string };

export class ApiError extends Error {
  status: number;
  code: string;
  reasons: Reason[];
  body: Record<string, unknown>;
  constructor(status: number, code: string, message: string, reasons: Reason[], body: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.code = code;
    this.reasons = reasons;
    this.body = body;
  }
}

let csrfToken = "";
export function setCsrf(token: string) {
  csrfToken = token;
}

async function handle<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; reasons?: Reason[] } } | null)?.error;
    throw new ApiError(
      res.status,
      err?.code ?? "HTTP_" + res.status,
      err?.message ?? `Request failed (${res.status})`,
      err?.reasons ?? [],
      (err as Record<string, unknown>) ?? {},
    );
  }
  return data as T;
}

/** Status 0 = no answer from the server: "TIMEOUT" (gave up waiting) or "NETWORK" (unreachable). Never a sign-out. */
export function isUnreachable(e: unknown): boolean {
  return e instanceof ApiError && e.status === 0;
}

export async function apiGet<T>(path: string, opts: { timeoutMs?: number } = {}): Promise<T> {
  const ctl = opts.timeoutMs ? new AbortController() : undefined;
  const timer = ctl ? setTimeout(() => ctl.abort(), opts.timeoutMs) : undefined;
  let res: Response;
  try {
    res = await fetch(path, { credentials: "same-origin", cache: "no-store", signal: ctl?.signal });
  } catch (e) {
    if (ctl?.signal.aborted) throw new ApiError(0, "TIMEOUT", "FlowSense took too long to answer.", [], {});
    throw new ApiError(0, "NETWORK", e instanceof Error ? e.message : "FlowSense could not be reached.", [], {});
  } finally {
    if (timer) clearTimeout(timer);
  }
  return handle<T>(res);
}

export async function apiPost<T>(path: string, body: unknown = {}, headers: Record<string, string> = {}, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, ...headers },
    body: JSON.stringify(body),
    signal,
  });
  return handle<T>(res);
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "DELETE", credentials: "same-origin", headers: { "X-CSRF-Token": csrfToken } });
  return handle<T>(res);
}

export async function apiPatch<T>(path: string, body: unknown = {}): Promise<T> {
  const res = await fetch(path, {
    method: "PATCH",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify(body),
  });
  return handle<T>(res);
}

export async function apiPut<T>(path: string, body: unknown = {}): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify(body),
  });
  return handle<T>(res);
}

// Multipart upload: the browser sets the multipart Content-Type (with boundary) itself.
/**
 * Multipart upload. With `onProgress` it uses XMLHttpRequest, the only browser API that reports how
 * much of the request body has been sent (0–1); answers and errors are handled exactly as for fetch.
 */
export async function apiUpload<T>(path: string, form: FormData, onProgress?: (sent: number) => void): Promise<T> {
  if (!onProgress) {
    const res = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrfToken },
      body: form,
    });
    return handle<T>(res);
  }
  const res = await new Promise<Response>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      const empty = xhr.status === 204 || xhr.status === 205 || xhr.status === 304;
      resolve(new Response(empty ? null : xhr.responseText, { status: xhr.status }));
    };
    xhr.onerror = () => reject(new TypeError("Network request failed"));
    xhr.onabort = () => reject(new TypeError("Upload cancelled"));
    xhr.send(form);
  });
  return handle<T>(res);
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
