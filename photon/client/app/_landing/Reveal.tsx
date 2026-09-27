"use client";

import { ReactNode, useEffect, useRef, useState } from "react";

const EASE = "cubic-bezier(.16,1,.3,1)";

function useInView<T extends HTMLElement>(threshold = 0.15) {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setSeen(true);
          io.disconnect(); // reveal once — re-animating on scroll-back reads as jitter
        }
      },
      { threshold, rootMargin: "0px 0px -8% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [threshold]);
  return [ref, seen] as const;
}

/** Fade + rise on first entry. */
export function Reveal({
  children,
  delay = 0,
  y = 28,
  className = "",
}: {
  children: ReactNode;
  delay?: number;
  y?: number;
  className?: string;
}) {
  const [ref, seen] = useInView<HTMLDivElement>();
  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: seen ? 1 : 0,
        transform: seen ? "none" : `translateY(${y}px)`,
        transition: `opacity .95s ${EASE} ${delay}ms, transform .95s ${EASE} ${delay}ms`,
        willChange: "opacity, transform",
      }}
    >
      {children}
    </div>
  );
}

/**
 * Word-by-word rise, the Camorent headline effect. Words are wrapped in an
 * overflow-hidden span so they slide up from behind the line above rather
 * than fading in place.
 */
export function WordsReveal({
  text,
  className = "",
  stagger = 45,
  delay = 0,
  serifWords = [],
}: {
  text: string;
  className?: string;
  stagger?: number;
  delay?: number;
  /** indices rendered in the display serif italic, for editorial emphasis */
  serifWords?: number[];
}) {
  const [ref, seen] = useInView<HTMLParagraphElement>(0.2);
  const words = text.split(" ");
  return (
    <p ref={ref} className={className}>
      {words.map((w, i) => (
        <span
          key={i}
          style={{ display: "inline-block", overflow: "hidden", verticalAlign: "bottom" }}
        >
          <span
            style={{
              display: "inline-block",
              transform: seen ? "none" : "translateY(105%)",
              opacity: seen ? 1 : 0,
              transition: `transform 1s ${EASE} ${delay + i * stagger}ms, opacity .8s ${EASE} ${delay + i * stagger}ms`,
              fontFamily: serifWords.includes(i) ? "var(--font-display)" : undefined,
              fontStyle: serifWords.includes(i) ? "italic" : undefined,
            }}
          >
            {w}
          </span>
          <span>&nbsp;</span>
        </span>
      ))}
    </p>
  );
}

/** The tracked small-caps label + hairline rule that opens every section. */
export function RuleLabel({ children, tone = "ink" }: { children: ReactNode; tone?: "ink" | "night" }) {
  return (
    <Reveal>
      <div className="flex items-center gap-4">
        <span
          className="h-px w-10 shrink-0"
          style={{ background: tone === "ink" ? "var(--l-rust)" : "var(--l-rust)" }}
        />
        <span
          className="text-[11px] tracking-[0.28em] uppercase"
          style={{ color: tone === "ink" ? "var(--l-muted)" : "rgba(255,253,248,.55)" }}
        >
          {children}
        </span>
        <span
          className="h-px flex-1"
          style={{ background: tone === "ink" ? "var(--l-rule)" : "rgba(255,253,248,.14)" }}
        />
      </div>
    </Reveal>
  );
}
