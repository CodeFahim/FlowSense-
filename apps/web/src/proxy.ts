import { NextResponse, type NextRequest } from "next/server";

// Runs before /api/* is rewritten to the API. Passes the client IP that Vercel saw (Vercel overwrites
// x-forwarded-for / x-real-ip, so a browser cannot forge them) together with a shared secret, so the
// API's rate limits can tell clients apart. Client-supplied x-lifeline-* headers are always removed.
export function proxy(request: NextRequest) {
  const headers = new Headers(request.headers);
  headers.delete("x-lifeline-proxy-secret");
  headers.delete("x-lifeline-client-ip");
  const secret = process.env.LIFELINE_PROXY_SECRET;
  if (secret) {
    const ip = request.headers.get("x-real-ip") ?? request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "";
    if (ip) {
      headers.set("x-lifeline-proxy-secret", secret);
      headers.set("x-lifeline-client-ip", ip);
    }
  }
  return NextResponse.next({ request: { headers } });
}

export const config = { matcher: "/api/:path*" };
