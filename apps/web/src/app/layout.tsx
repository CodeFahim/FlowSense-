import type { Metadata, Viewport } from "next";
import { MotionProvider } from "@/components/motion";
import "./globals.css";

export const metadata: Metadata = {
  title: "FlowSense — cash-flow rescue (sandbox)",
  description: "Sandbox-only cash-flow rescue copilot for small businesses. Synthetic data; no real banking.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Keep the chat composer above the on-screen keyboard on mobile.
  interactiveWidget: "resizes-content",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f8" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1114" },
  ],
};

// Applies the saved appearance before first paint so the page does not flash.
// The server-side preference is the source of truth; this is only a local cache of it.
const THEME_BOOT = `(function(){try{var a=JSON.parse(localStorage.getItem("lifeline.appearance")||"{}");var t=a.theme||"system";if(t==="system"){t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}var r=document.documentElement;r.dataset.theme=t;r.dataset.accent=a.accent||"teal"}catch(e){}})();`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="min-h-screen antialiased">
        <MotionProvider>{children}</MotionProvider>
      </body>
    </html>
  );
}
