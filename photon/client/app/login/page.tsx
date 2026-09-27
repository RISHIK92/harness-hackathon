"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { githubLoginUrl, login, signup } from "@/lib/api";
import PhotonMark from "../_landing/PhotonMark";
import { useCycle } from "../_landing/cycle";

/** The same welcome, in the languages a caller might use. */
const WELCOME = ["welcome", "స్వాగతం", "வணக்கம்", "स्वागत"];

const CONNECTS = ["GitHub", "Slack", "Jira", "Linear", "Notion", "Datadog"];

function GithubGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="currentColor" aria-hidden>
      <path d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38l-.01-1.34c-2.23.48-2.7-1.07-2.7-1.07-.36-.93-.89-1.18-.89-1.18-.73-.5.06-.49.06-.49.8.06 1.23.83 1.23.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48l-.01 2.2c0 .21.15.46.55.38A8 8 0 0 0 8 0Z" />
    </svg>
  );
}

/** A hairline-underlined field: the rule charges rust from the left on focus,
 *  which is the same gesture the landing page uses for every other rule. */
function Field({
  label,
  ...props
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="l-field block">
      <span
        className="l-field-label block text-[10px] tracking-[0.26em] uppercase"
        style={{ color: "var(--l-muted)" }}
      >
        {label}
      </span>
      <input
        {...props}
        className="mt-2 w-full bg-transparent pb-2 text-[18px] outline-none"
        style={{ color: "var(--l-ink)" }}
      />
      <span className="relative block h-px" style={{ background: "var(--l-rule)" }}>
        <span className="l-field-rule" style={{ background: "var(--l-rust)" }} />
      </span>
    </label>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const greeting = useCycle(WELCOME.length, 3400);

  // The active-tab rule is measured rather than hard-coded: the labels are
  // prose and a magic pixel width would drift the moment the copy changes.
  const tabsRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState({ left: 0, width: 0 });
  useEffect(() => {
    const host = tabsRef.current;
    if (!host) return;
    // A ResizeObserver rather than one measurement on mount: the labels are set
    // in a webfont, so a single early read lands on the fallback metrics and
    // the rule ends up the wrong length until something else forces a re-read.
    const ro = new ResizeObserver(() => {
      const el = host.querySelector<HTMLElement>(`[data-mode="${mode}"]`);
      if (el) setTab({ left: el.offsetLeft, width: el.offsetWidth });
    });
    ro.observe(host);
    return () => ro.disconnect();
  }, [mode]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (mode === "signup" && password.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    setBusy(true);
    try {
      await (mode === "login" ? login(email.trim(), password) : signup(email.trim(), password));
      router.push("/home");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="l-landing grid min-h-screen md:grid-cols-[1.05fr_1fr]">
      {/* the paper side: the mark arrives here exactly as it does on the landing page */}
      <section className="relative hidden overflow-hidden px-10 py-12 md:flex md:flex-col md:justify-between">
        <div
          aria-hidden
          className="pointer-events-none absolute -left-40 -top-52 h-[620px] w-[620px] rounded-full"
          style={{ border: "1px solid rgba(180,83,9,.10)" }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 flex select-none items-center justify-center"
        >
          <span
            key={WELCOME[greeting]}
            className="l-ghost italic leading-none whitespace-nowrap"
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "clamp(120px, 15vw, 260px)",
              color: "rgba(28,25,23,.045)",
            }}
          >
            {WELCOME[greeting]}
          </span>
        </div>

        <div className="relative flex items-center gap-4">
          <span className="l-rule-grow h-px w-10 shrink-0" style={{ background: "var(--l-rust)" }} />
          <span
            className="l-fade text-[11px] tracking-[0.3em] whitespace-nowrap uppercase"
            style={{ color: "var(--l-muted)", animationDelay: "240ms" }}
          >
            Photon — build no. 001
          </span>
          <span
            className="l-rule-grow h-px flex-1"
            style={{ background: "var(--l-rule)", animationDelay: "160ms" }}
          />
        </div>

        <div className="relative">
          <PhotonMark fontSize="clamp(64px, 8vw, 120px)" />
          <p
            className="l-fade mt-8 max-w-sm text-[16px] leading-relaxed"
            style={{ color: "var(--l-ink-2)", animationDelay: "820ms" }}
          >
            Sign in and hand it your repos, docs and threads. It reads them once, then
            sits on every call — answering in whatever language it was asked, and naming
            its source every time.
          </p>
        </div>

        <div className="l-fade relative" style={{ animationDelay: "980ms" }}>
          <div className="text-[10px] tracking-[0.26em] uppercase" style={{ color: "var(--l-muted)" }}>
            What it will read
          </div>
          <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2">
            {CONNECTS.map((c) => (
              <span
                key={c}
                className="flex items-center gap-2 text-[11px] tracking-[0.16em] uppercase"
                style={{ color: "var(--l-ink-2)" }}
              >
                <span className="l-dot" style={{ background: "var(--l-rule)" }} />
                {c}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* the form side */}
      <section
        className="relative flex items-center justify-center px-6 py-14 md:px-12"
        style={{ borderLeft: "1px solid var(--l-rule)", background: "rgba(255,253,248,.6)" }}
      >
        <div className="w-full max-w-sm">
          <div className="mb-10 md:hidden">
            <PhotonMark fontSize="56px" />
          </div>

          {/* mode switch: two labels and a rule that slides between them */}
          <div ref={tabsRef} className="relative flex gap-7">
            {(["login", "signup"] as const).map((m) => (
              <button
                key={m}
                type="button"
                data-mode={m}
                onClick={() => {
                  setMode(m);
                  setError(null);
                }}
                className="pb-3 text-[11px] tracking-[0.24em] uppercase transition-colors"
                style={{ color: mode === m ? "var(--l-ink)" : "var(--l-muted)" }}
              >
                {m === "login" ? "Sign in" : "Create account"}
              </button>
            ))}
            <span className="absolute inset-x-0 bottom-0 h-px" style={{ background: "var(--l-rule)" }} />
            <span
              className="absolute bottom-0 h-px transition-all duration-500"
              style={{
                background: "var(--l-rust)",
                left: tab.left,
                width: tab.width,
                transitionTimingFunction: "cubic-bezier(.16,1,.3,1)",
              }}
            />
          </div>

          <form onSubmit={submit} className="mt-10 flex flex-col gap-7">
            <Field
              label="Email"
              type="email"
              required
              autoComplete="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <Field
              label="Password"
              type="password"
              required
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder={mode === "signup" ? "At least 8 characters" : "••••••••"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />

            <button
              type="submit"
              disabled={busy}
              className="l-submit relative mt-1 overflow-hidden rounded-full px-6 py-3.5 text-[12px] tracking-[0.2em] uppercase transition-transform hover:-translate-y-0.5 disabled:translate-y-0"
              style={{ background: "var(--l-ink)", color: "var(--l-paper)" }}
            >
              {/* a photon crossing the button, instead of a spinner */}
              {busy && <span aria-hidden className="l-submit-sweep" />}
              <span className="relative">
                {busy ? "One moment" : mode === "login" ? "Sign in" : "Create account"}
              </span>
            </button>
          </form>

          {error && (
            <p
              className="l-note mt-6 pl-4 text-[13px] leading-relaxed"
              style={{ color: "var(--l-ink-2)", borderLeft: "1px solid var(--l-rust)" }}
            >
              {error}
            </p>
          )}

          <div className="my-8 flex items-center gap-3">
            <span className="h-px flex-1" style={{ background: "var(--l-rule)" }} />
            <span className="text-[10px] tracking-[0.26em] uppercase" style={{ color: "var(--l-muted)" }}>
              or
            </span>
            <span className="h-px flex-1" style={{ background: "var(--l-rule)" }} />
          </div>

          <a
            href={githubLoginUrl()}
            className="flex items-center justify-center gap-3 rounded-full px-6 py-3.5 text-[12px] tracking-[0.2em] uppercase transition-colors"
            style={{ border: "1px solid var(--l-rule)", color: "var(--l-ink)" }}
          >
            <GithubGlyph />
            Continue with GitHub
          </a>

          <p className="mt-8 text-[12px] leading-relaxed" style={{ color: "var(--l-muted)" }}>
            {mode === "login"
              ? "A personal workspace is waiting; connect a source and it starts reading."
              : "You get a personal workspace immediately. Nothing is read until you connect a source and pick what it may see."}
          </p>
        </div>
      </section>
    </div>
  );
}
