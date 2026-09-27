"use client";

import { useEffect, useRef, useState } from "react";
import { map, useSectionProgress } from "./scroll";

const FRAMES = [
  { n: "01", t: "Hear", ms: "0ms", d: "Speech-to-text transcribes the linked speaker. Script detection picks the reply language — Telugu, Tamil, Hindi, English." },
  { n: "02", t: "Triage", ms: "0ms", d: "A regex gate, no model and no network: greetings get an instant line, side conversation gets silence, anything question-shaped goes to the pipeline." },
  { n: "03", t: "Plan", ms: "~1.0s", d: "One planner call picks the tools the question actually needs — usually one, sometimes two. It never guesses a repo id; the loop forces that." },
  { n: "04", t: "Gather", ms: "~0.4s", d: "Tool calls run in parallel across code, docs, Slack, Jira, tickets, accounts, logs and the provenance graph. Each returns evidence with a real locator." },
  { n: "05", t: "Compose", ms: "~0.9s", d: "Thirty-five words, spoken-shaped, every claim tagged with the evidence ids it came from." },
  { n: "06", t: "Verify", ms: "1ms", d: "Deterministic, no model. Any claim whose citations don't resolve is stripped. Too many stripped and the whole turn abstains." },
];

/**
 * Horizontally-scrolled frames, pinned vertically — the filmstrip move. The
 * track is translated by the section's own progress, so the browser's native
 * scroll does the easing and there is no scroll hijacking to fight.
 */
export default function TurnStrip() {
  const ref = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const p = useSectionProgress(ref);
  const [travel, setTravel] = useState(0);

  useEffect(() => {
    const measure = () => {
      const el = trackRef.current;
      if (el) setTravel(Math.max(0, el.scrollWidth - window.innerWidth + 48));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const x = -map(p, 0.08, 0.95, 0, travel);

  return (
    <section
      id="turn"
      ref={ref}
      className="relative"
      style={{ height: "420vh", background: "var(--l-night)" }}
    >
      <div className="sticky top-0 flex h-screen flex-col justify-center overflow-hidden">
        <div className="px-6 md:px-10">
          <div className="mx-auto flex max-w-6xl items-center gap-4">
            <span className="h-px w-10" style={{ background: "var(--l-rust)" }} />
            <span
              className="text-[11px] tracking-[0.28em] uppercase"
              style={{ color: "rgba(255,253,248,.55)" }}
            >
              How one turn runs
            </span>
            <span className="h-px flex-1" style={{ background: "rgba(255,253,248,.12)" }} />
            <span
              className="text-[11px] tracking-[0.28em] uppercase tabular-nums"
              style={{ color: "rgba(255,253,248,.35)" }}
            >
              {String(Math.min(6, Math.floor(p * 6) + 1)).padStart(2, "0")} / 06
            </span>
          </div>
        </div>

        <div
          ref={trackRef}
          className="mt-12 flex gap-6 px-6 md:px-10"
          style={{ transform: `translate3d(${x}px,0,0)`, willChange: "transform" }}
        >
          {FRAMES.map((f, i) => {
            const near = map(p, (i - 1.4) / 6, (i + 0.2) / 6, 0, 1);
            return (
              <article
                key={f.n}
                className="shrink-0 rounded-xl p-7"
                style={{
                  width: "min(78vw, 380px)",
                  minHeight: 340,
                  background: "rgba(255,253,248,.035)",
                  border: "1px solid rgba(255,253,248,.10)",
                  opacity: 0.35 + near * 0.65,
                  transform: `translateY(${(1 - near) * 22}px)`,
                  transition: "opacity .25s linear",
                }}
              >
                <div className="flex items-baseline justify-between">
                  <span
                    className="italic leading-none"
                    style={{
                      fontFamily: "var(--font-display)",
                      fontSize: 56,
                      color: "rgba(255,253,248,.16)",
                    }}
                  >
                    {f.n}
                  </span>
                  <span
                    className="font-mono text-[11px]"
                    style={{ color: "var(--l-terra)" }}
                  >
                    {f.ms}
                  </span>
                </div>
                <h3
                  className="mt-6 text-[26px] leading-tight"
                  style={{ color: "var(--l-paper)" }}
                >
                  {f.t}
                </h3>
                <p
                  className="mt-4 text-[14px] leading-relaxed"
                  style={{ color: "rgba(255,253,248,.62)" }}
                >
                  {f.d}
                </p>
              </article>
            );
          })}
        </div>

        <div className="mt-12 px-6 md:px-10">
          <div className="mx-auto max-w-6xl">
            <div className="h-px w-full" style={{ background: "rgba(255,253,248,.12)" }}>
              <div
                className="h-px origin-left"
                style={{ background: "var(--l-terra)", transform: `scaleX(${p})` }}
              />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
