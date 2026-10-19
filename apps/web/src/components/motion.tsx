"use client";

/*
  FlowSense motion presets. One place for timing, so the whole app moves the same way:
  - gentle springs (no visible bounce) and 150–300 ms fades, transform and opacity only;
  - MotionConfig reducedMotion="user": with the system's reduced-motion setting, transforms and layout
    animations are dropped and only short fades remain; imperative animations (dialogs) check the same;
  - motion never carries meaning on its own: every state also has text and an icon.
*/

import { AnimatePresence, LayoutGroup, MotionConfig, animate, motion, useReducedMotion, type Transition, type Variants } from "motion/react";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export { AnimatePresence, LayoutGroup, motion, useReducedMotion };

export const spring: Transition = { type: "spring", stiffness: 420, damping: 36, mass: 0.8 };
export const softSpring: Transition = { type: "spring", stiffness: 260, damping: 30 };
export const fast: Transition = { duration: 0.15, ease: [0.2, 0.8, 0.2, 1] };
export const base: Transition = { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] };

export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <MotionConfig reducedMotion="user" transition={spring}>
      {children}
    </MotionConfig>
  );
}

/* ------------------------------------------------------------------ entrances */

export const riseVariants: Variants = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: spring },
};

// Inside a <Stagger>, children only declare variants and the parent times them; outside, they animate on mount.
const StaggerCtx = createContext(false);

/** Children that use <Rise> (or Card / Stat) enter one after another, 40 ms apart. */
export function Stagger({ children, className, as = "div", gap = 0.04, ...rest }: { children: ReactNode; className?: string; as?: "div" | "section" | "ul" | "ol"; gap?: number; "aria-label"?: string; "aria-labelledby"?: string }) {
  const Comp = motion[as];
  return (
    <StaggerCtx.Provider value={true}>
      <Comp className={className} initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: gap } } }} {...rest}>
        {children}
      </Comp>
    </StaggerCtx.Provider>
  );
}

/** Props for an element that should rise in: staggered when inside <Stagger>, on mount otherwise. */
export function useRise() {
  const staggered = useContext(StaggerCtx);
  return staggered ? { variants: riseVariants } : { variants: riseVariants, initial: "hidden" as const, animate: "show" as const };
}

export function Rise({ children, className, as = "div", ...rest }: { children: ReactNode; className?: string; as?: "div" | "section" | "li" | "header"; id?: string; "aria-label"?: string; "aria-labelledby"?: string }) {
  const Comp = motion[as];
  return <Comp className={className} {...rest} {...useRise()}>{children}</Comp>;
}

/** Content that replaces a loading placeholder fades and rises in, so data never just pops onto the page. */
export function Reveal({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <motion.div className={className} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring}>
      {children}
    </motion.div>
  );
}

/* ------------------------------------------------------------------ lists */

/**
 * List items that animate when they are added, removed or reordered. Neighbours slide into place
 * (layout transforms), so the list never jumps. Keys must be stable record ids.
 */
export const itemMotion = {
  layout: "position" as const,
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, x: -12, transition: fast },
  transition: spring,
};

export function ListPresence({ children }: { children: ReactNode }) {
  // initial={false}: items already there on first render don't animate (the page entrance covers them).
  return <AnimatePresence initial={false}>{children}</AnimatePresence>;
}

/* ------------------------------------------------------------------ changing figures */

/**
 * A figure that changes after the backend confirms new data: the old value slides out, the new one
 * slides in with a brief tint. The exact formatted value is always what is shown (no counting up).
 */
export function AnimatedValue({ value, className }: { value: string; className?: string }) {
  return (
    <span className={"relative inline-grid overflow-hidden align-bottom " + (className ?? "")}>
      <AnimatePresence initial={false} mode="popLayout">
        <motion.span
          key={value}
          className="relative col-start-1 row-start-1 rounded-md px-0.5 -mx-0.5"
          initial={{ opacity: 0, y: "70%" }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: "-70%", transition: fast }}
          transition={spring}
        >
          {/* Tint behind a changed value, fading out (only on changes: AnimatePresence skips the first render). */}
          <motion.span aria-hidden className="absolute inset-0 -z-10 rounded-md bg-accent-soft" initial={{ opacity: 1 }} animate={{ opacity: 0 }} transition={{ duration: 1.2, ease: "easeOut" }} />
          {value}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

/* ------------------------------------------------------------------ dialogs */

/**
 * Keeps showing the last non-null value for `ms` after it becomes null, so a closing dialog keeps its
 * title and content while it animates out; afterwards it is null again (the content unmounts, and the
 * next opening starts fresh).
 */
export function useExitValue<T>(value: T | null, ms = 260): T | null {
  const [last, setLast] = useState<T | null>(value);
  if (value !== null && value !== last) setLast(value);
  useEffect(() => {
    if (value !== null) return;
    const t = setTimeout(() => setLast(null), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return value ?? last;
}

export type DialogMotion = "modal" | "left" | "right" | "sheet";

function reduced() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

type Offset = { x?: string | number; y?: string | number; scale?: number };

function offscreen(kind: DialogMotion): Offset {
  if (kind === "left") return { x: "-100%" };
  if (kind === "right") return { x: "100%" };
  if (kind === "sheet") return window.matchMedia("(min-width: 640px)").matches ? { x: "100%" } : { y: "100%" };
  return { y: 10, scale: 0.97 };
}

/** Plays the opening animation of a native <dialog> (box and backdrop). */
export function dialogIn(d: HTMLDialogElement, kind: DialogMotion) {
  if (reduced()) {
    animate(d, { opacity: [0, 1] }, { duration: 0.12 });
    return;
  }
  const from = offscreen(kind);
  animate(d, { opacity: [0, 1], x: [from.x ?? 0, 0], y: [from.y ?? 0, 0], scale: [from.scale ?? 1, 1] }, kind === "modal" ? spring : softSpring);
  d.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: "ease-out", pseudoElement: "::backdrop" });
}

/** Plays the closing animation, then resolves; the caller closes the dialog afterwards. */
export async function dialogOut(d: HTMLDialogElement, kind: DialogMotion) {
  const to: Offset = reduced() ? {} : offscreen(kind);
  d.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: "ease-in", pseudoElement: "::backdrop", fill: "forwards" });
  await animate(d, { opacity: 0, ...(to.x !== undefined ? { x: to.x } : {}), ...(to.y !== undefined ? { y: to.y } : {}), ...(to.scale !== undefined ? { scale: to.scale } : {}) }, { duration: reduced() ? 0.1 : 0.17, ease: [0.4, 0, 1, 1] }).finished;
}

/** Resets inline styles left by the closing animation, so the next opening starts clean. */
export function dialogReset(d: HTMLDialogElement) {
  d.style.opacity = "";
  d.style.transform = "";
  // Only the dialog's own and its backdrop's animations; descendants (spinners) are left alone.
  d.getAnimations({ subtree: true }).forEach((a) => {
    const fx = a.effect as KeyframeEffect | null;
    if (fx?.target === d) a.cancel();
  });
}
