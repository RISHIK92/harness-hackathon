"use client";

/** Your agent asking you something, live, from a meeting it is attending
 * for you.
 *
 * Mounted once inside AuthGuard so it is on every signed-in screen: the
 * person being asked is rarely looking at the page the call started from.
 * It polls rather than subscribing — the same trade the whisper panel makes —
 * and the countdown is the SERVER's, re-read each poll, so a slow tab can
 * never show "12s left" on a question the agent has already apologised for.
 *
 * A browser notification is raised once per new question, because a
 * 60-second window is too short to rely on someone glancing at a tab.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  answerEscalation,
  declineEscalation,
  getToken,
  myEscalations,
  type Escalation,
} from "@/lib/api";

const POLL_MS = 3000;

function notify(e: Escalation) {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const n = new Notification("Your agent needs you", {
      body: `${e.meeting_title ? e.meeting_title + ": " : ""}${e.question}`,
      tag: e.id,
      requireInteraction: true,
    });
    n.onclick = () => window.focus();
  } catch {
    /* notifications blocked or unsupported — the panel still shows */
  }
}

function Card({ e, fetchedAt, onDone }: { e: Escalation; fetchedAt: number; onDone: () => void }) {
  const [draft, setDraft] = useState(e.draft_answer ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Counts down from the SERVER's figure as of the last poll, so the number
  // moves every second without ever trusting this machine's clock against
  // the server's deadline.
  const [now, setNow] = useState(fetchedAt);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const left = Math.max(0, e.seconds_left - Math.floor(Math.max(0, now - fetchedAt) / 1000));

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-amber-300 bg-white p-3 text-sm text-slate-900 shadow-xl dark:border-amber-700 dark:bg-slate-900 dark:text-slate-100">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
          {e.meeting_title || "Live call"} · {e.topic}
        </span>
        <span className={`tabular-nums text-xs font-semibold ${left <= 15 ? "text-rose-600" : "opacity-70"}`}>
          {left}s
        </span>
      </div>
      <p className="font-medium">“{e.question}”</p>
      {e.context.length > 0 && (
        <details className="mt-1 text-xs opacity-70">
          <summary className="cursor-pointer">What led here</summary>
          <ul className="mt-1 space-y-0.5">
            {e.context.map((c, i) => (
              <li key={i}>
                <span className="font-semibold">{c.role === "agent" ? "Agent" : "Them"}:</span> {c.text}
              </li>
            ))}
          </ul>
        </details>
      )}
      <textarea
        autoFocus
        rows={2}
        className="mt-2 w-full rounded border px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-950"
        placeholder="Your answer — the agent will say it on the call, credited to you"
        value={draft}
        onChange={(ev) => setDraft(ev.target.value)}
        onKeyDown={(ev) => {
          if (ev.key === "Enter" && !ev.shiftKey && draft.trim()) {
            ev.preventDefault();
            void act(() => answerEscalation(e.id, draft));
          }
        }}
      />
      {error && <p className="text-xs text-rose-600">{error}</p>}
      <div className="mt-2 flex gap-2">
        <button
          disabled={busy || !draft.trim()}
          onClick={() => void act(() => answerEscalation(e.id, draft))}
          className="flex-1 rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
        >
          Send to the call
        </button>
        <button
          disabled={busy}
          onClick={() => void act(() => declineEscalation(e.id))}
          className="rounded border px-3 py-1.5 text-xs dark:border-slate-700"
          title="The agent apologises and promises a follow-up"
        >
          Skip
        </button>
      </div>
    </div>
  );
}

export default function EscalationInbox() {
  const [open, setOpen] = useState<Escalation[]>([]);
  const [fetchedAt, setFetchedAt] = useState(0);
  const seen = useRef<Set<string>>(new Set());

  const poll = useCallback(async () => {
    if (!getToken()) return;
    try {
      const all = await myEscalations();
      const live = all.filter((e) => e.status === "open" && e.seconds_left > 0);
      for (const e of live) {
        if (!seen.current.has(e.id)) {
          seen.current.add(e.id);
          notify(e);
        }
      }
      setOpen(live);
      setFetchedAt(Date.now());
    } catch {
      /* signed out or offline — try again next tick */
    }
  }, []);

  useEffect(() => {
    void poll();
    const t = setInterval(() => void poll(), POLL_MS);
    return () => clearInterval(t);
  }, [poll]);

  // Turning alerts on lives in the sidebar (AppShell); this only shows the
  // questions themselves.
  if (!open.length) return null;
  return (
    <div className="fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2">
      {open.map((e) => (
        <Card key={e.id} e={e} fetchedAt={fetchedAt} onDone={() => void poll()} />
      ))}
    </div>
  );
}
