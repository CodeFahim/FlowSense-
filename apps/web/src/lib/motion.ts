"use client";

import { useSyncExternalStore } from "react";

// CSS motion is switched off globally in globals.css; JavaScript-driven motion (charts) asks here.
const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void) {
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

/** True when the user asked the system for less motion. Server render assumes reduced (no animation). */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia(QUERY).matches, () => true);
}
