import type { NextConfig } from "next";

// Same-origin routing: the browser only talks to this Next.js server, which
// proxies /api/* to FastAPI. Session cookies therefore stay first-party and no
// CORS is needed. API_ORIGIN is fixed at build time (Docker build arg).
const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";
// On a hosted build the API must be a public HTTPS origin, never localhost or a Docker hostname.
if (process.env.VERCEL && !/^https:\/\/[^/]+$/.test(process.env.API_ORIGIN ?? "")) {
  throw new Error("Set API_ORIGIN to the deployed API origin, e.g. https://lifeline-api.onrender.com (no trailing slash).");
}

// Content Security Policy: only this origin for scripts, styles, fonts, images and API calls; no framing,
// no plugins, no form posts elsewhere. Next.js needs inline bootstrap scripts ('unsafe-inline'); React renders
// all user and AI text as text nodes, so no user content is ever injected as HTML. Dev mode also needs eval.
const dev = process.env.NODE_ENV !== "production";
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
  // Renamed pages: keep old links working.
  async redirects() {
    return [
      { source: "/audit", destination: "/activity", permanent: false },
      { source: "/invoices", destination: "/money-in", permanent: false },
      { source: "/planner", destination: "/rescue", permanent: false },
    ];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
