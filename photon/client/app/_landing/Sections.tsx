"use client";

import Link from "next/link";
import { Reveal, RuleLabel, WordsReveal } from "./Reveal";
import { useSignedIn } from "./scroll";

const FEATURES = [
  {
    k: "01",
    t: "It joins the call",
    d: "A real LiveKit participant with speech in and out. Poke the button or say its name and it takes the mic from whoever addressed it — which is also how it knows whose private sources may answer.",
  },
  {
    k: "02",
    t: "It reads the screen",
    d: "Share your screen and ask what you are looking at. The frame becomes citable evidence like anything else, and stale frames expire rather than describing a screen that closed twenty minutes ago.",
  },
  {
    k: "03",
    t: "It keeps the room",
    d: "Every call gets an eight-character code with no 0, O, 1, l or I in it — the first thing anyone does with a code is read it aloud. The code is the room, the link and the transcript.",
  },
  {
    k: "04",
    t: "It shows its work",
    d: "A live trace beside the captions: which tool is running right now, what it cost in milliseconds, which evidence card each citation chip resolves to.",
  },
  {
    k: "05",
    t: "It knows which repo",
    d: "Fifteen repositories in a workspace and no repo named in the question? Search runs across all of them and relevance decides — rather than silently answering about the wrong one.",
  },
  {
    k: "06",
    t: "It stays in its lane",
    d: "Workspaces are the tenant boundary. Every tool call is scoped by the server, never by the planner, so a hallucinated id is a dropped argument and not a cross-tenant read.",
  },
];

/**
 * Hairline glyphs, 20x20, stroke-only so they sit on the paper like the
 * rules do rather than like UI chrome.
 */
const GLYPH: Record<string, React.ReactNode> = {
  ramp: (
    <>
      <circle cx="10" cy="10" r="7.2" />
      <path d="M10 5.6V10l3 2" />
    </>
  ),
  read: (
    <>
      <path d="M10 5.6c-1.6-1.1-3.4-1.4-5.4-1.1v9.9c2-.3 3.8 0 5.4 1.1 1.6-1.1 3.4-1.4 5.4-1.1V4.5c-2-.3-3.8 0-5.4 1.1Z" />
      <path d="M10 5.6v9.9" />
    </>
  ),
  hours: (
    <>
      <circle cx="10" cy="10" r="7.2" />
      <path d="M10 2.8v14.4" />
      <path d="M2.8 10h4M13.2 10h4" />
    </>
  ),
  speed: (
    <>
      <circle cx="10" cy="11.4" r="6.2" />
      <path d="M10 11.4 13 8.4" />
      <path d="M7.8 2.9h4.4M10 2.9v2.3" />
    </>
  ),
  tongue: (
    <>
      <circle cx="10" cy="10" r="7.2" />
      <path d="M2.8 10h14.4" />
      <path d="M10 2.8c1.9 2 2.9 4.5 2.9 7.2s-1 5.2-2.9 7.2c-1.9-2-2.9-4.5-2.9-7.2s1-5.2 2.9-7.2Z" />
    </>
  ),
  leave: (
    <>
      <path d="M11.5 3.4H5.2A1.2 1.2 0 0 0 4 4.6v10.8a1.2 1.2 0 0 0 1.2 1.2h6.3" />
      <path d="M13 7.2 15.8 10 13 12.8" />
      <path d="M15.8 10H8.2" />
    </>
  ),
  unknown: (
    <>
      <circle cx="10" cy="10" r="7.2" />
      <path d="M7.9 8a2.1 2.1 0 1 1 2.9 2c-.6.3-.9.8-.9 1.5v.4" />
      <path d="M10 14.4h.01" />
    </>
  ),
};

function Glyph({ name }: { name: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.15"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {GLYPH[name]}
    </svg>
  );
}

/** The three numbers that are actually measured, not argued. */
const HIRE_STATS = [
  { n: "17s", l: "to ingest a 236-file repo" },
  { n: "1.47s", l: "median answer, on the call" },
  { n: "4", l: "languages, picked per turn" },
];

const HIRE = [
  {
    g: "ramp",
    q: "Time to the first useful answer",
    human: "Weeks of onboarding, and months before the odd corners are known",
    photon: "One ingest — about 17 seconds for a 236-file repo",
  },
  {
    g: "read",
    q: "How much of it has been read",
    human: "Whatever there was time for, plus whoever was around to ask",
    photon: "Every repo, doc, thread, ticket and incident you connect",
  },
  {
    g: "hours",
    q: "When it is available",
    human: "Working hours, one conversation at a time",
    photon: "Every call at once, in whichever room the code was read into",
  },
  {
    g: "speed",
    q: "How long the answer takes",
    human: "“Let me check and get back to you”",
    photon: "Under a second and a half, while the customer is still on the line",
  },
  {
    g: "tongue",
    q: "Languages on the call",
    human: "Whichever ones you managed to hire for",
    photon: "English, Hindi, Telugu and Tamil, picked per turn",
  },
  {
    g: "leave",
    q: "What happens when they leave",
    human: "The context leaves with them",
    photon: "The workspace keeps it; the next person inherits all of it",
  },
  {
    g: "unknown",
    q: "When it does not know",
    human: "Sometimes guesses, and sounds confident doing it",
    photon: "Abstains, says so out loud, and hands the call back to you",
  },
];

const SOURCES = ["GitHub", "Slack", "Jira", "Linear", "Notion", "Datadog", "Tickets", "Incidents", "Runbooks", "Commits"];

const RULES = [
  { n: "I", t: "No uncited claim", d: "If a sentence has no evidence id behind it, the verifier removes the sentence." },
  { n: "II", t: "Abstain over guess", d: "“I don't have evidence for that” is a correct answer. A confident wrong one is not." },
  { n: "III", t: "Never fabricate a locator", d: "An invented file path, line number, ticket id or Slack timestamp is a build-breaking bug, not a UI nit." },
];

export function Statement() {
  return (
    <section className="relative px-6 py-40 md:px-10 md:py-56">
      <div className="mx-auto max-w-4xl">
        <RuleLabel>The problem</RuleLabel>
        <WordsReveal
          text="The answer already exists. It is in a Slack thread from March, a commit message, a ticket nobody linked."
          serifWords={[2, 3]}
          className="mt-10 text-[clamp(28px,4.4vw,58px)] leading-[1.12]"
          stagger={32}
        />
        <Reveal delay={220}>
          <p
            className="mt-10 max-w-2xl text-[16px] leading-relaxed"
            style={{ color: "var(--l-ink-2)" }}
          >
            The person on the call does not have three minutes to find it, the new hire
            has not been here long enough to know it, and the LLM that has read none of
            it will happily invent something that sounds right. Photon looks it up while
            you are still talking, and shows you exactly where it came from.
          </p>
        </Reveal>
      </div>
    </section>
  );
}

export function Hiring() {
  return (
    <section id="hiring" className="relative px-6 py-32 md:px-10 md:py-44">
      <div className="mx-auto max-w-5xl">
        <RuleLabel>Instead of hiring for it</RuleLabel>
        <WordsReveal
          text="The teammate who has already read everything."
          serifWords={[6]}
          className="mt-10 text-[clamp(30px,4.4vw,56px)] leading-[1.1]"
          stagger={38}
        />
        <Reveal delay={200}>
          <p className="mt-8 max-w-2xl text-[16px] leading-relaxed" style={{ color: "var(--l-ink-2)" }}>
            Support and solutions engineering is mostly recall: knowing which commit
            changed that behaviour, which thread decided it, which customer is on the
            old plan. That knowledge takes months to build in a person and walks out
            of the door with them. Photon is the part of the job that is recall — so
            the people you do hire spend their time on the part that is judgement.
          </p>
        </Reveal>

        {/* the measured numbers, before the qualitative rows */}
        <div className="mt-14 grid gap-8 sm:grid-cols-3">
          {HIRE_STATS.map((st, i) => (
            <Reveal key={st.l} delay={i * 110}>
              <div className="border-t pt-5" style={{ borderColor: "var(--l-rust)" }}>
                <div
                  className="leading-none"
                  style={{ fontFamily: "var(--font-display)", fontSize: 46, color: "var(--l-ink)" }}
                >
                  {st.n}
                </div>
                <div className="mt-3 text-[12px] leading-relaxed" style={{ color: "var(--l-muted)" }}>
                  {st.l}
                </div>
              </div>
            </Reveal>
          ))}
        </div>

        <div className="mt-20">
          <Reveal>
            <div
              className="hidden grid-cols-[28px_1.05fr_1fr_1fr] gap-6 border-b pb-4 md:grid"
              style={{ borderColor: "var(--l-rule)" }}
            >
              <span />
              <span />
              <span className="text-[11px] tracking-[0.22em] uppercase" style={{ color: "var(--l-muted)" }}>
                A new hire
              </span>
              <span
                className="flex items-center gap-2 text-[11px] tracking-[0.22em] uppercase"
                style={{ color: "var(--l-rust)" }}
              >
                <span className="l-dot" style={{ background: "var(--l-rust)" }} />
                Photon
              </span>
            </div>
          </Reveal>
          {HIRE.map((r, i) => (
            <Reveal key={r.q} delay={i * 70}>
              <div
                className="l-row group relative grid gap-x-6 gap-y-3 border-b py-6 md:grid-cols-[28px_1.05fr_1fr_1fr]"
                style={{ borderColor: "var(--l-rule)" }}
              >
                {/* the rule under the row redraws itself in rust on hover */}
                <span className="l-row-rule" style={{ background: "var(--l-rust)" }} />

                <span className="l-row-glyph mt-0.5" style={{ color: "var(--l-muted)" }}>
                  <Glyph name={r.g} />
                </span>

                <span className="text-[15px] leading-snug" style={{ color: "var(--l-ink)" }}>
                  {r.q}
                </span>

                <span
                  className="l-row-was text-[14px] leading-relaxed"
                  style={{ color: "var(--l-muted)" }}
                >
                  {r.human}
                </span>

                <span
                  className="flex gap-2 text-[14px] leading-relaxed"
                  style={{ color: "var(--l-ink-2)" }}
                >
                  <span
                    aria-hidden
                    className="l-row-tick mt-[7px] h-px w-3 shrink-0"
                    style={{ background: "var(--l-rust)" }}
                  />
                  {r.photon}
                </span>
              </div>
            </Reveal>
          ))}
        </div>

        <Reveal delay={160}>
          <p className="mt-10 max-w-2xl text-[14px] leading-relaxed" style={{ color: "var(--l-muted)" }}>
            It is not a replacement for the person who decides what to do about the
            answer. It is a replacement for the hour they would have spent finding it —
            and it escalates the moment the evidence runs out, rather than filling the
            gap with something plausible.
          </p>
        </Reveal>
      </div>
    </section>
  );
}

export function Features() {
  return (
    <section className="relative px-6 py-32 md:px-10 md:py-44">
      <div className="mx-auto max-w-6xl">
        <RuleLabel>Capabilities</RuleLabel>
        <div className="mt-16 grid gap-x-12 gap-y-16 md:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f, i) => (
            <Reveal key={f.k} delay={(i % 3) * 110}>
              <div className="group">
                <div
                  className="h-px w-full origin-left scale-x-100 transition-transform duration-700"
                  style={{ background: "var(--l-rule)" }}
                />
                <div
                  className="mt-5 text-[11px] tracking-[0.24em] uppercase"
                  style={{ color: "var(--l-rust)" }}
                >
                  {f.k}
                </div>
                <h3
                  className="mt-4 text-[24px] leading-tight"
                  style={{ color: "var(--l-ink)" }}
                >
                  {f.t}
                </h3>
                <p
                  className="mt-3 text-[14px] leading-relaxed"
                  style={{ color: "var(--l-ink-2)" }}
                >
                  {f.d}
                </p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

export function Sources() {
  const row = [...SOURCES, ...SOURCES];
  return (
    <section id="sources" className="relative overflow-hidden py-28">
      <div className="px-6 md:px-10">
        <div className="mx-auto max-w-6xl">
          <RuleLabel>Connected sources</RuleLabel>
        </div>
      </div>
      <div className="mt-12 flex" style={{ maskImage: "linear-gradient(90deg,transparent,#000 12%,#000 88%,transparent)", WebkitMaskImage: "linear-gradient(90deg,transparent,#000 12%,#000 88%,transparent)" }}>
        <div className="l-marquee flex shrink-0 gap-14 pr-14">
          {row.map((s, i) => (
            <span
              key={i}
              className="whitespace-nowrap italic leading-none"
              style={{
                fontFamily: "var(--font-display)",
                fontSize: "clamp(44px,6vw,84px)",
                color: i % 3 === 0 ? "var(--l-ink)" : "rgba(28,25,23,.22)",
              }}
            >
              {s}
            </span>
          ))}
        </div>
        <div className="l-marquee flex shrink-0 gap-14 pr-14" aria-hidden>
          {row.map((s, i) => (
            <span
              key={i}
              className="whitespace-nowrap italic leading-none"
              style={{
                fontFamily: "var(--font-display)",
                fontSize: "clamp(44px,6vw,84px)",
                color: i % 3 === 0 ? "var(--l-ink)" : "rgba(28,25,23,.22)",
              }}
            >
              {s}
            </span>
          ))}
        </div>
      </div>
      <div className="mt-12 px-6 md:px-10">
        <Reveal>
          <p
            className="mx-auto max-w-2xl text-center text-[15px] leading-relaxed"
            style={{ color: "var(--l-ink-2)" }}
          >
            Point it at the repositories you choose, the channels you pick and the projects
            that matter. Read-only scopes, credentials encrypted at rest, and nothing
            indexed that you did not select.
          </p>
        </Reveal>
      </div>
    </section>
  );
}

export function Rules() {
  return (
    <section id="rules" className="relative px-6 py-32 md:px-10 md:py-44">
      <div className="mx-auto max-w-5xl">
        <RuleLabel>Three rules it cannot break</RuleLabel>
        <div className="mt-16 space-y-px">
          {RULES.map((r, i) => (
            <Reveal key={r.n} delay={i * 130}>
              <div
                className="grid items-baseline gap-6 border-t py-10 md:grid-cols-[80px_1fr_1.2fr]"
                style={{ borderColor: "var(--l-rule)" }}
              >
                <span
                  className="italic leading-none"
                  style={{
                    fontFamily: "var(--font-display)",
                    fontSize: 44,
                    color: "var(--l-rust)",
                  }}
                >
                  {r.n}
                </span>
                <h3 className="text-[26px] leading-tight" style={{ color: "var(--l-ink)" }}>
                  {r.t}
                </h3>
                <p className="text-[15px] leading-relaxed" style={{ color: "var(--l-ink-2)" }}>
                  {r.d}
                </p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

export function Closing() {
  const signedIn = useSignedIn();
  // Signed out, every product link is really a sign-in link — sending someone
  // to /call or /dashboard first only bounces them.
  const gate = (href: string) => (signedIn ? href : "/login");
  return (
    <section className="relative overflow-hidden px-6 py-44 md:px-10">
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/2 h-[720px] w-[720px] -translate-x-1/2 -translate-y-1/2 rounded-full"
        style={{ border: "1px solid rgba(180,83,9,.12)" }}
      />
      <div className="relative mx-auto max-w-3xl text-center">
        <Reveal>
          <p
            className="italic leading-[0.9]"
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "clamp(64px,11vw,150px)",
              color: "var(--l-ink)",
            }}
          >
            ask it live
          </p>
        </Reveal>
        <Reveal delay={140}>
          <p
            className="mx-auto mt-8 max-w-lg text-[16px] leading-relaxed"
            style={{ color: "var(--l-ink-2)" }}
          >
            Open a room, share the code, and let it listen. Or connect your sources
            first and find out how much of next week&apos;s questions it can already
            answer.
          </p>
        </Reveal>
        <Reveal delay={240}>
          <div className="mt-12 flex flex-wrap items-center justify-center gap-3">
            <Link
              href={gate("/call")}
              className="rounded-full px-7 py-3.5 text-[13px] tracking-[0.16em] uppercase transition-transform hover:-translate-y-0.5"
              style={{ background: "var(--l-ink)", color: "var(--l-paper)" }}
            >
              {signedIn ? "Join a call" : "Sign in to join a call"}
            </Link>
            <Link
              href={gate("/dashboard")}
              className="rounded-full px-7 py-3.5 text-[13px] tracking-[0.16em] uppercase"
              style={{ border: "1px solid var(--l-rule)", color: "var(--l-ink)" }}
            >
              {signedIn ? "Open dashboard" : "Connect your sources"}
            </Link>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

export function Footer() {
  const signedIn = useSignedIn();
  const links: [string, string][] = signedIn
    ? [
        ["Join a call", "/call"],
        ["Dashboard", "/dashboard"],
        ["Sign out", "/login"],
      ]
    : [
        ["Join a call", "/login"],
        ["Dashboard", "/login"],
        ["Sign in", "/login"],
      ];
  return (
    <footer className="px-6 pb-14 md:px-10">
      <div
        className="mx-auto flex max-w-6xl flex-col gap-6 border-t pt-10 md:flex-row md:items-center md:justify-between"
        style={{ borderColor: "var(--l-rule)" }}
      >
        <span
          className="text-[26px] italic leading-none"
          style={{ fontFamily: "var(--font-display)", color: "var(--l-ink)" }}
        >
          photon
        </span>
        <div className="flex flex-wrap gap-6">
          {links.map(([l, h]) => (
            <Link
              key={l}
              href={h}
              className="text-[12px] tracking-[0.16em] uppercase"
              style={{ color: "var(--l-muted)" }}
            >
              {l}
            </Link>
          ))}
        </div>
        <span
          className="text-[11px] tracking-[0.22em] uppercase"
          style={{ color: "var(--l-muted)" }}
        >
          Company brain · live call support
        </span>
      </div>
    </footer>
  );
}
