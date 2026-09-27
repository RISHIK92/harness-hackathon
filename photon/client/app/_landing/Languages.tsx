"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { map, useSectionProgress } from "./scroll";

type Turn = {
  code: string;
  label: string;
  native: string;
  q: string;
  a: string;
  ms: string;
};

/**
 * Real shapes: the Telugu line is the one that was actually put through
 * speech-to-text on a live call, and the timings are the measured
 * end-to-end turns for each language, not illustrative numbers.
 *
 * (Vendors are deliberately unnamed here and in the UI copy: what a reader
 * is being told is that this works in their language and how long it takes,
 * and the supplier behind that has already changed once.)
 */
const TURNS: Turn[] = [
  {
    code: "en-IN",
    label: "English",
    native: "English",
    q: "Why is pricing different in Bangalore?",
    a: "Bangalore is a partner city — a reseller agreement gives partner-tier accounts a 0.88x rate there.",
    ms: "2281ms",
  },
  {
    code: "hi-IN",
    label: "Hindi",
    native: "हिन्दी",
    q: "बेंगलुरु में कीमतें अलग क्यों हैं? कारण बताइए।",
    a: "बेंगलुरु एक पार्टनर शहर है — एक रीसेलर समझौते के कारण पार्टनर-टियर खातों को वहाँ 0.88x दर मिलती है।",
    ms: "2459ms",
  },
  {
    code: "te-IN",
    label: "Telugu",
    native: "తెలుగు",
    q: "బెంగళూరులో ధరలు ఎందుకు వేరుగా ఉన్నాయి? కారణం చెప్పండి.",
    a: "బెంగళూరు ఒక భాగస్వామ్య నగరం — రీసెల్లర్ ఒప్పందం వల్ల పార్ట్‌నర్-టైర్ ఖాతాలకు అక్కడ 0.88x రేటు వర్తిస్తుంది.",
    ms: "3619ms",
  },
  {
    code: "ta-IN",
    label: "Tamil",
    native: "தமிழ்",
    q: "பெங்களூரில் விலை ஏன் வேறுபடுகிறது? காரணம் சொல்லுங்கள்.",
    a: "பெங்களூரு ஒரு பங்குதாரர் நகரம் — ரீசெல்லர் ஒப்பந்தத்தால் பார்ட்னர்-டியர் கணக்குகளுக்கு அங்கு 0.88x கட்டணம் பொருந்தும்.",
    ms: "2922ms",
  },
];

const WORD_MS = 95; // per-word cadence of the "spoken" reveal
const IDLE_MS = 1400; // no scroll for this long and the card drives itself
const HOLD_AFTER_SPEECH_MS = 150; // beat between the last word and the next language

/** 34 bars whose resting heights look sampled rather than uniform. */
const BARS = Array.from({ length: 34 }, (_, i) => 5 + ((i * 37 + i * i * 7) % 19));

/**
 * One spoken turn. Remounted per language (keyed by code) so the word-by-word
 * cadence replays from the start instead of cross-blending two scripts —
 * and so `done` resets without a setState in an effect body.
 */
function SpokenTurn({ turn, onDone }: { turn: Turn; onDone: () => void }) {
  const words = turn.a.split(" ");
  const speakMs = 420 + words.length * WORD_MS;
  const [done, setDone] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => {
      setDone(true);
      onDone();
    }, speakMs);
    return () => clearTimeout(t);
  }, [speakMs, onDone]);

  return (
    <>
      <div className="flex items-center justify-between">
        <span
          className="font-mono text-[11px] tracking-[0.14em]"
          style={{ color: "var(--l-rust)" }}
        >
          {turn.code}
        </span>
        <span
          className="flex items-center gap-2 font-mono text-[11px]"
          style={{ color: done ? "var(--l-muted)" : "var(--l-rust)" }}
        >
          <span className={done ? "l-dot" : "l-dot l-dot-live"} />
          {done ? `spoken · ${turn.ms}` : "speaking"}
        </span>
      </div>

      <div className="mt-7 flex items-center gap-2">
        <span className="text-[11px] tracking-[0.24em] uppercase" style={{ color: "var(--l-muted)" }}>
          Caller
        </span>
        <span className="l-mic" aria-hidden />
      </div>
      <p className="mt-2 text-[16px] leading-relaxed" style={{ color: "var(--l-ink-2)" }}>
        {turn.q}
      </p>

      <div className="mt-7 text-[11px] tracking-[0.24em] uppercase" style={{ color: "var(--l-rust)" }}>
        Photon
      </div>
      <p className="mt-2 text-[17px] leading-relaxed" style={{ color: "var(--l-ink)" }}>
        {words.map((w, i) => (
          <span
            key={i}
            className="l-word"
            style={{ animationDelay: `${420 + i * WORD_MS}ms` }}
          >
            {w}
            {i < words.length - 1 ? " " : ""}
          </span>
        ))}
      </p>

      {/* the voice itself: bars ride while it speaks, then settle flat */}
      <div
        className={`mt-8 flex h-10 items-center gap-[3px] ${done ? "l-wave-off" : "l-wave-on"}`}
        aria-hidden
      >
        {BARS.map((h, i) => (
          <span
            key={i}
            className="l-bar"
            style={{ height: h, animationDelay: `${(i % 9) * 90}ms` }}
          />
        ))}
      </div>
    </>
  );
}

export default function Languages() {
  const ref = useRef<HTMLElement>(null);
  const p = useSectionProgress(ref);

  // Scroll drives the language while the page is moving...
  const scrollIdx = Math.min(
    TURNS.length - 1,
    Math.max(0, Math.floor(map(p, 0.05, 0.95, 0, TURNS.length))),
  );
  // ...and this offset carries the timer's advances when it is not.
  const [offset, setOffset] = useState(0);
  const [pinnedIdx, setPinnedIdx] = useState<number | null>(null);
  const [idle, setIdle] = useState(true);
  // which language has finished being "spoken" — compared against the current
  // one, so a scroll that changes the card can never inherit a stale finish
  const [spokenCode, setSpokenCode] = useState<string | null>(null);

  useEffect(() => {
    let t: ReturnType<typeof setTimeout>;
    const onScroll = () => {
      // React bails out when these already hold the same value, so this is
      // cheap even though scroll fires constantly.
      setOffset(0);
      setPinnedIdx(null);
      setIdle(false);
      setSpokenCode(null);
      clearTimeout(t);
      t = setTimeout(() => setIdle(true), IDLE_MS);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      clearTimeout(t);
    };
  }, []);

  const i = pinnedIdx ?? (scrollIdx + offset) % TURNS.length;
  const turn = TURNS[i];
  const onDone = useCallback(() => setSpokenCode(turn.code), [turn.code]);

  // The card advances when the voice finishes, not on a fixed clock — and
  // only while the reader is neither scrolling nor holding a language.
  const auto = idle && pinnedIdx === null && spokenCode === turn.code;
  useEffect(() => {
    if (!auto) return;
    const t = setTimeout(() => {
      setOffset((o) => o + 1);
      setSpokenCode(null);
    }, HOLD_AFTER_SPEECH_MS);
    return () => clearTimeout(t);
  }, [auto]);

  return (
    <section id="languages" ref={ref} className="relative" style={{ height: "340vh" }}>
      <div className="sticky top-0 flex h-screen items-center overflow-hidden px-6 md:px-10">
        <div className="mx-auto w-full max-w-5xl">
          <div className="flex items-center gap-4">
            <span className="h-px w-10" style={{ background: "var(--l-rust)" }} />
            <span
              className="text-[11px] tracking-[0.28em] uppercase"
              style={{ color: "var(--l-muted)" }}
            >
              Answers in your caller&apos;s language
            </span>
            <span className="h-px flex-1" style={{ background: "var(--l-rule)" }} />
            <span
              className="font-mono text-[11px] tracking-[0.2em]"
              style={{ color: "var(--l-muted)" }}
            >
              {String(i + 1).padStart(2, "0")} / 0{TURNS.length}
            </span>
          </div>

          <div className="mt-10 grid gap-12 md:grid-cols-[0.9fr_1.1fr]">
            <div>
              <h2
                className="text-[clamp(28px,3.6vw,46px)] leading-[1.1]"
                style={{ color: "var(--l-ink)" }}
              >
                English, Hindi,{" "}
                <span style={{ fontFamily: "var(--font-display)", fontStyle: "italic" }}>
                  Telugu
                </span>{" "}
                and{" "}
                <span style={{ fontFamily: "var(--font-display)", fontStyle: "italic" }}>
                  Tamil
                </span>
                .
              </h2>
              <p className="mt-6 text-[15px] leading-relaxed" style={{ color: "var(--l-ink-2)" }}>
                The language is detected from the script of what was said — Telugu, Tamil
                and Devanagari sit in disjoint Unicode blocks, so it costs nothing and no
                extra model call. Code-mixed speech counts too: a plurality of Indic
                letters is enough, so “sir webhook fail అవుతోంది” comes back in Telugu.
              </p>
              <p className="mt-5 text-[15px] leading-relaxed" style={{ color: "var(--l-ink-2)" }}>
                The corpus stays English. The answer is composed in the language it was
                asked in, and every citation id survives the translation character for
                character — so the verifier still strips an uncited claim, in-language.
              </p>

              <div className="mt-9 flex flex-wrap gap-2">
                {TURNS.map((t, n) => (
                  <button
                    key={t.code}
                    onClick={() => setPinnedIdx(n)}
                    className="rounded-full px-4 py-2 text-[12px] tracking-[0.12em] transition-colors"
                    style={{
                      border: "1px solid var(--l-rule)",
                      background: n === i ? "var(--l-ink)" : "transparent",
                      color: n === i ? "var(--l-paper)" : "var(--l-ink-2)",
                    }}
                  >
                    {t.native}
                  </button>
                ))}
              </div>
              <p className="mt-4 text-[11px] tracking-[0.16em] uppercase" style={{ color: "var(--l-muted)" }}>
                {pinnedIdx !== null
                  ? "Held — scroll to resume"
                  : idle
                    ? "Advances when the voice finishes — scroll to steer"
                    : "Following your scroll"}
              </p>
            </div>

            <div
              className="relative rounded-2xl p-7 md:p-8"
              style={{
                background: "var(--l-paper)",
                border: "1px solid var(--l-rule)",
                boxShadow: "0 40px 100px -60px rgba(28,25,23,.45)",
                minHeight: 400,
              }}
            >
              <div key={turn.code} className="l-lang-in">
                <SpokenTurn turn={turn} onDone={onDone} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
