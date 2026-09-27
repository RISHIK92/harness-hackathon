"use client";

import { RefObject, useEffect, useState, useSyncExternalStore } from "react";
import { getToken } from "@/lib/api";

/**
 * Progress (0..1) of an element through the viewport, measured the way a
 * pinned/sticky section wants it: 0 when its top hits the top of the
 * viewport, 1 when its bottom does. Sections that are exactly one viewport
 * tall never move, which is why every pinned section here is taller.
 */
export function useSectionProgress(ref: RefObject<HTMLElement | null>) {
  const [p, setP] = useState(0);

  useEffect(() => {
    let raf = 0;
    const read = () => {
      raf = 0;
      const el = ref.current;
      if (!el) return;
      const total = el.offsetHeight - window.innerHeight;
      if (total <= 0) return setP(0);
      const travelled = -el.getBoundingClientRect().top;
      setP(Math.min(1, Math.max(0, travelled / total)));
    };
    // rAF-coalesced: scroll fires far faster than we can usefully paint.
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(read);
    };
    read();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [ref]);

  return p;
}

/** Whole-document scroll progress, for the hairline bar at the top. */
export function useDocProgress() {
  const [p, setP] = useState(0);
  useEffect(() => {
    let raf = 0;
    const read = () => {
      raf = 0;
      const total = document.documentElement.scrollHeight - window.innerHeight;
      setP(total <= 0 ? 0 : Math.min(1, window.scrollY / total));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(read);
    };
    read();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);
  return p;
}

/** Linear map with clamping — the one primitive every scroll effect here uses. */
export function map(p: number, a: number, b: number, from: number, to: number) {
  if (b === a) return from;
  const t = Math.min(1, Math.max(0, (p - a) / (b - a)));
  return from + (to - from) * t;
}

/**
 * Whether a token is in localStorage, read without a setState-in-effect.
 * The server snapshot is `false`, so the first paint matches SSR and the
 * real value swaps in on hydration.
 */
export function useSignedIn() {
  return useSyncExternalStore(
    () => () => {},
    () => Boolean(getToken()),
    () => false,
  );
}
