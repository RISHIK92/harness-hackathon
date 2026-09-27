"use client";

/** Whisper, as a side panel on the call itself.
 *
 * The premise is that the call in front of you is the CLIENT'S meeting —
 * Meet, Zoom, whatever — and Photon is listening over your shoulder rather
 * than sitting in it. So this panel is private: what appears here is yours,
 * enforced server-side by the thread's user_id, and nothing about it is
 * visible to anyone else on the call.
 *
 * What gets captured is everyone EXCEPT you and Photon. Your own lines are
 * not questions to answer, and Photon answering its own output would loop.
 * The caption stream already carries that distinction (`isLocal`, `speaker`),
 * so the rule is exact rather than heuristic — see the bridge in page.tsx.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  askInWhisperThread,
  createWhisperSession,
  listWhisperLines,
  myWhisperThread,
  whisperMessages,
  type WhisperMessage,
} from "@/lib/api";
import { stripCitations } from "@/lib/evidence";

const POLL_MS = 2000;

function Warnings({ items }: { items: WhisperMessage["warnings"] }) {
  if (!items?.length) {
    return (
      <p className="mt-1.5 text-[11px]" style={{ color: "var(--l-moss, #3f6212)" }}>
        ✅ Safe to say as-is
      </p>
    );
  }
  return (
    <div className="mt-1.5 space-y-1">
      {items.map((w) => (
        <p key={w.code} className="text-[11px] leading-snug" style={{ color: "var(--l-rust)" }}>
          <span className="font-semibold">⚠️ {w.label}</span>
          <span className="opacity-75"> — {w.detail}</span>
        </p>
      ))}
    </div>
  );
}

export default function WhisperPanel({
  meetingCode,
  sessionId,
  onSession,
}: {
  meetingCode: string;
  sessionId: string | null;
  onSession: (id: string | null) => void;
}) {
  /** How many client lines the SERVER has, so "it is listening" is visible
   *  rather than a claim — counted where the lines land, not where the
   *  browser thinks it sent them. Silence with no counter looks identical to
   *  a broken feed. */
  const [heardCount, setHeardCount] = useState(0);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<WhisperMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const start = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await createWhisperSession({
        title: `Call ${meetingCode}`,
        source: "photon",
        meeting_slug: meetingCode || undefined,
      });
      const thread = await myWhisperThread(s.id);
      setThreadId(thread.id);
      onSession(s.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [meetingCode, onSession]);

  useEffect(() => {
    if (!threadId) return;
    let live = true;
    const tick = async () => {
      try {
        const [m, lines] = await Promise.all([
          whisperMessages(threadId),
          sessionId ? listWhisperLines(sessionId) : Promise.resolve([]),
        ]);
        if (!live) return;
        setMessages(m);
        setHeardCount(lines.filter((l) => l.is_client).length);
      } catch {
        /* a dropped poll is not worth an error state; the next one tells us */
      }
    };
    void tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [threadId, sessionId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const ask = async () => {
    if (!draft.trim() || !threadId) return;
    const question = draft.trim();
    setDraft("");
    setBusy(true);
    try {
      await askInWhisperThread(threadId, question);
      setMessages(await whisperMessages(threadId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!sessionId || !threadId) {
    return (
      <div className="h-full overflow-y-auto px-5 py-4">
        <p className="text-[13px] leading-relaxed l-t-2">
          Photon reads what the client asks on this call and suggests answers only to you.
          Nobody else sees this panel.
        </p>
        <button
          onClick={() => void start()}
          disabled={busy}
          className="mt-3 rounded px-3 py-1.5 text-[12px] tracking-[0.06em] disabled:opacity-50"
          style={{ background: "rgba(180,83,9,.10)", color: "var(--l-rust)" }}
        >
          {busy ? "starting…" : "Start listening"}
        </button>
        {error && (
          <p className="mt-3 text-[12px]" style={{ color: "var(--l-rust)" }}>
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div
        className="flex items-center gap-2 px-5 py-2 text-[11px] tracking-[0.08em]"
        style={{ borderBottom: "1px solid var(--l-rule)", color: "var(--l-ink-2)" }}
      >
        <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: "#16a34a" }} />
        listening · {heardCount} line{heardCount === 1 ? "" : "s"} heard
        <span className="flex-1" />
        <span className="l-t-muted">private to you</span>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {messages.map((m) => {
          const mine = m.role === "member";
          return (
            <div
              key={m.id}
              className="rounded-lg px-3 py-2"
              style={{
                background: mine ? "rgba(28,25,23,.04)" : "rgba(180,83,9,.06)",
                marginLeft: mine ? "1.5rem" : 0,
              }}
            >
              {m.trigger_text && (
                <p
                  className="mb-1.5 border-l-2 pl-2 text-[11px] italic"
                  style={{ borderColor: "var(--l-rule)", color: "var(--l-muted)" }}
                >
                  they asked: {m.trigger_text}
                </p>
              )}
              <p className="text-[13px] leading-relaxed" style={{ color: "var(--l-ink)" }}>
                {stripCitations(m.text)}
              </p>
              {!mine && <Warnings items={m.warnings} />}
            </div>
          );
        })}
        {!messages.length && (
          <p className="text-[13px] leading-relaxed l-t-2">
            Nothing yet. When they ask something, a suggestion appears here — with a note on
            whether it is safe to say as-is. You can also ask Photon anything privately.
          </p>
        )}
        <div ref={bottom} />
      </div>

      <div className="flex gap-2 px-5 py-3" style={{ borderTop: "1px solid var(--l-rule)" }}>
        <input
          className="flex-1 rounded px-2.5 py-1.5 text-[13px]"
          style={{ background: "rgba(28,25,23,.04)", color: "var(--l-ink)" }}
          placeholder="Ask Photon privately…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void ask()}
        />
        <button
          onClick={() => void ask()}
          disabled={busy}
          className="rounded px-3 text-[12px] disabled:opacity-50"
          style={{ background: "rgba(180,83,9,.10)", color: "var(--l-rust)" }}
        >
          {busy ? "…" : "Ask"}
        </button>
      </div>
      {error && (
        <p className="px-5 pb-2 text-[11px]" style={{ color: "var(--l-rust)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
