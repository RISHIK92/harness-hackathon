"use client";

import EvidencePanel from "./EvidencePanel";
import type { AgentAnswer } from "@/lib/evidence";

type Turn = { role: "user" | "agent"; question?: string; result?: AgentAnswer };

const EXAMPLES = [
  "Why does pricing have a special case for Bangalore?",
  "Why are Northwind's webhooks failing?",
  "How many times do we retry a failed webhook?",
];

/** Ask Photon, and read the answer with its citations. The idle state offers
 *  three real questions rather than an empty box — nobody's first instinct is
 *  to guess what a company brain can be asked. */
export default function AskPanel({
  turns,
  value,
  busy,
  onChange,
  onAsk,
}: {
  turns: Turn[];
  value: string;
  busy: boolean;
  onChange: (v: string) => void;
  onAsk: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {turns.length === 0 ? (
          // Centred rather than pinned to the top: with nothing asked yet, a
          // short block at the top of a full-height panel reads as a page
          // that failed to load the rest of itself.
          <div className="flex h-full flex-col justify-center">
            <p className="text-[15px] leading-relaxed l-t-2">
              Ask anything grounded in what this workspace has read. Every claim comes back with
              a citation you can open, and it abstains rather than guessing.
            </p>

            <div className="mt-10 flex items-center gap-3">
              <span className="h-px w-6 shrink-0" style={{ background: "var(--l-rust)" }} />
              <span className="text-[10px] tracking-[0.24em] whitespace-nowrap uppercase l-t-muted">
                Try
              </span>
              <span className="h-px flex-1" style={{ background: "var(--l-rule)" }} />
            </div>

            <div className="mt-1">
              {EXAMPLES.map((q) => (
                <button
                  key={q}
                  onClick={() => onChange(q)}
                  className="l-row group relative flex w-full items-baseline gap-3 border-b py-3.5 text-left"
                  style={{ borderColor: "var(--l-rule)" }}
                >
                  <span className="l-row-rule" style={{ background: "var(--l-rust)" }} />
                  <span className="flex-1 text-[14px] leading-snug" style={{ color: "var(--l-ink-2)" }}>
                    {q}
                  </span>
                  <span
                    className="shrink-0 text-[13px] opacity-0 transition-opacity group-hover:opacity-100"
                    style={{ color: "var(--l-rust)" }}
                    aria-hidden
                  >
                    →
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <EvidencePanel turns={turns} />
        )}
      </div>

      <div className="border-t px-5 py-4" style={{ borderColor: "var(--l-rule)" }}>
        <div className="flex gap-2">
          <input
            className="l-input"
            placeholder="Ask Photon…"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onAsk()}
          />
          <button onClick={onAsk} disabled={busy || !value.trim()} className="l-btn">
            {busy ? "Asking…" : "Ask"}
          </button>
        </div>
        <p className="mt-3 text-[11px] l-t-muted">
          Typed questions never interrupt the call — nothing is spoken aloud.
        </p>
      </div>
    </div>
  );
}
