"use client";

import Link from "next/link";
import { useRef } from "react";
import { map, useSectionProgress, useSignedIn } from "./scroll";
import { useCycle } from "./cycle";
import PhotonMark from "./PhotonMark";

/** What it can take in — lit in sequence, in step with the beam. */
const MODES = ["voice", "screen", "code", "docs", "threads"];

/** The watermark, in the languages a caller might actually use. */
const GHOST = ["evidence", "సాక్ష్యం", "சான்று", "प्रमाण"];

/** One turn, compressed to three beats. Real shapes from the live stack. */
const BEATS = [
  { k: "heard", v: "te-IN · poked by Alice" },
  { k: "planned", v: "search_code + explain_why" },
  { k: "answered", v: "1.5s · 3 citations" },
];

/** The oversized watermark, drifting on scroll and rotating through scripts. */
function Ghost({ y }: { y: number }) {
  const i = useCycle(GHOST.length, 3800);
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 flex select-none items-center justify-center"
      style={{ transform: `translateY(${y}px)` }}
    >
      <span
        key={GHOST[i]}
        className="l-ghost italic leading-none"
        style={{
          fontFamily: "var(--font-display)",
          fontSize: "clamp(130px, 21vw, 340px)",
          color: "rgba(28,25,23,.045)",
          whiteSpace: "nowrap",
          letterSpacing: "-0.02em",
        }}
      >
        {GHOST[i]}
      </span>
    </div>
  );
}

/** A turn, running in one line, so "agentic" is shown rather than asserted. */
function Ticker() {
  const i = useCycle(BEATS.length, 1900);
  const beat = BEATS[i];
  return (
    <div className="flex items-center gap-3 font-mono text-[11px]">
      <span className="l-dot l-dot-live" />
      <span key={beat.k} className="l-beat" style={{ color: "var(--l-muted)" }}>
        <span style={{ color: "var(--l-rust)" }}>{beat.k}</span>
        <span> · {beat.v}</span>
      </span>
    </div>
  );
}

/**
 * The opening. A single particle crosses the page — a streak that collapses
 * into a point at the head of the mark — and where it lands, the word
 * unfolds out of it, letter by letter, each one splitting into a warm and a
 * cool ghost that recombine as it settles. The whole arrival is over in
 * about a second and a half and never repeats, which is the point of the
 * name.
 */
export default function Hero() {
  const ref = useRef<HTMLDivElement>(null);
  const p = useSectionProgress(ref);
  const signedIn = useSignedIn();

  const ghostY = map(p, 0, 1, 30, -90);
  const drift = map(p, 0, 1, 0, -60);
  const cue = map(p, 0, 0.2, 1, 0);

  return (
    <div ref={ref} className="relative" style={{ height: "150vh" }}>
      <div className="sticky top-0 h-screen overflow-hidden">
        <Ghost y={ghostY} />

        <div
          aria-hidden
          className="pointer-events-none absolute -right-40 -top-40 h-[560px] w-[560px] rounded-full"
          style={{ border: "1px solid rgba(180,83,9,.10)" }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-72 -left-56 h-[680px] w-[680px] rounded-full"
          style={{ border: "1px solid rgba(28,25,23,.05)" }}
        />

        <div
          className="relative mx-auto flex h-full max-w-6xl flex-col justify-center px-6 md:px-10"
          style={{ transform: `translateY(${drift}px)` }}
        >
          <div className="flex items-center gap-4">
            <span className="l-rule-grow h-px w-10 shrink-0" style={{ background: "var(--l-rust)" }} />
            <span
              className="l-fade text-[11px] tracking-[0.3em] whitespace-nowrap uppercase"
              style={{ color: "var(--l-muted)", animationDelay: "260ms" }}
            >
              Photon — build no. 001
            </span>
            <span className="l-rule-grow h-px flex-1" style={{ background: "var(--l-rule)", animationDelay: "160ms" }} />
          </div>

          {/* one particle arrives; the word is what it leaves behind */}
          <PhotonMark as="h1" fontSize="clamp(72px, 11.5vw, 176px)" className="mt-6" />

          {/* what it can take in, lit left to right behind the beam */}
          <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2">
            {MODES.map((m, i) => (
              <span
                key={m}
                className="l-mode flex items-center gap-2 text-[11px] tracking-[0.22em] uppercase"
                style={{ color: "var(--l-muted)", animationDelay: `${820 + i * 80}ms` }}
              >
                <span className="l-spark" style={{ animationDelay: `${820 + i * 80}ms` }} />
                {m}
              </span>
            ))}
          </div>

          <div
            className="l-fade mt-8 space-y-1 text-[clamp(20px,2.6vw,32px)] leading-[1.28]"
            style={{ color: "var(--l-ink)", animationDelay: "760ms" }}
          >
            <p>The employee who has</p>
            <p>
              already read{" "}
              <span style={{ fontFamily: "var(--font-display)", fontStyle: "italic" }}>
                everything
              </span>
              .
            </p>
            <p style={{ color: "var(--l-muted)" }}>On every call. From day one.</p>
          </div>

          <p
            className="l-fade mt-7 max-w-xl text-[15px] leading-relaxed"
            style={{ color: "var(--l-ink-2)", animationDelay: "860ms" }}
          >
            Photon joins your customer calls having read every repo, doc, Slack thread and
            ticket you connect, and answers in about a second and a half — with a citation behind
            every claim, or nothing at all.
          </p>

          <div
            className="l-fade mt-9 flex flex-wrap items-center gap-3"
            style={{ animationDelay: "940ms" }}
          >
            <Link
              href={signedIn ? "/call" : "/login"}
              className="rounded-full px-6 py-3 text-[13px] tracking-[0.16em] uppercase transition-transform hover:-translate-y-0.5"
              style={{ background: "var(--l-ink)", color: "var(--l-paper)" }}
            >
              {signedIn ? "Join a call" : "Sign in to join a call"}
            </Link>
            <Link
              href={signedIn ? "/home" : "/login"}
              className="rounded-full px-6 py-3 text-[13px] tracking-[0.16em] uppercase transition-colors"
              style={{ border: "1px solid var(--l-rule)", color: "var(--l-ink)" }}
            >
              {signedIn ? "Open dashboard" : "Connect your sources"}
            </Link>
          </div>

          <div className="l-fade mt-9" style={{ animationDelay: "1080ms" }}>
            <Ticker />
          </div>
        </div>

        <div
          className="absolute inset-x-0 bottom-8 flex flex-col items-center gap-3"
          style={{ opacity: cue }}
        >
          <span
            className="text-[10px] tracking-[0.34em] uppercase"
            style={{ color: "var(--l-muted)" }}
          >
            Scroll
          </span>
          <span className="l-scroll-line" />
        </div>
      </div>
    </div>
  );
}
