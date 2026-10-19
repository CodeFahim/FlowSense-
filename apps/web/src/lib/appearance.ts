// Theme and accent. The user's saved preference (server) is authoritative; a copy is
// kept in localStorage only so the next page load can paint in the right theme.
export type Theme = "system" | "light" | "dark";
export type Accent = "teal" | "blue" | "violet" | "emerald" | "amber";

export const ACCENTS: { id: Accent; label: string; swatch: string }[] = [
  { id: "teal", label: "Teal", swatch: "#0f766e" },
  { id: "blue", label: "Blue", swatch: "#2457d6" },
  { id: "violet", label: "Violet", swatch: "#6a46d9" },
  { id: "emerald", label: "Emerald", swatch: "#0d7f56" },
  { id: "amber", label: "Amber", swatch: "#a75208" },
];

const KEY = "flowsense.appearance";
let mql: MediaQueryList | null = null;
let current: { theme: Theme; accent: Accent } = { theme: "system", accent: "teal" };

function paint() {
  const root = document.documentElement;
  const dark = current.theme === "dark" || (current.theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.dataset.theme = dark ? "dark" : "light";
  root.dataset.accent = current.accent;
}

export function applyAppearance(theme: Theme, accent: Accent) {
  current = { theme, accent };
  try {
    localStorage.setItem(KEY, JSON.stringify(current));
  } catch {
    // Storage can be unavailable (private mode); the theme still applies for this page.
  }
  paint();
  if (!mql) {
    mql = window.matchMedia("(prefers-color-scheme: dark)");
    mql.addEventListener("change", () => current.theme === "system" && paint());
  }
}

export function resolvedTheme(): "light" | "dark" {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}
