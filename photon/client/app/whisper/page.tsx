"use client";

/** Whisper mode.
 *
 * Two panes on purpose. Left is the MEETING — what was actually said, shared,
 * the thing the client can also hear. Right is YOURS — suggestions and your
 * own questions, which the client cannot see and which the server enforces by
 * user_id, not by this component hiding anything.
 *
 * It polls rather than subscribing: there is no room connection here, because
 * the meeting is usually on a platform we have no session with. That is the
 * whole premise of whisper mode.
 */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import AppShell from "../_ui/AppShell";
import { Badge, Card, Empty, ErrorNote, PageHeader, timeAgo } from "../_ui/kit";
import {
  addWhisperLine,
  askInWhisperThread,
  createWhisperSession,
  endWhisperSession,
  joinThirdPartyMeeting,
  stopWhisperBot,
  whisperBotState,
  type WhisperBotState,
  listWhisperLines,
  listWhisperSessions,
  myWhisperThread,
  whisperMessages,
  type WhisperLine,
  type WhisperMessage,
  type WhisperSession,
} from "../../lib/api";
import { stripCitations } from "../../lib/evidence";

const POLL_MS = 2500;

const SOURCE: Record<string, string> = { photon: "Photon call", external: "Meet extension", bot: "Notetaker", manual: "Manual" };
const sourceOf = (s: WhisperSession) =>
  s.source === "external" && !(s.external_ref ?? "").startsWith("meet:") ? "Manual" : SOURCE[s.source] ?? s.source;

function Warnings({ items }: { items: WhisperMessage["warnings"] }) {
  if (!items?.length) {
    return <p className="mt-2 text-[12px]" style={{ color: "var(--p-ok)" }}>✓ Safe to say as-is — grounded in client-safe sources.</p>;
  }
  return (
    <div className="mt-2 space-y-1">
      {items.map((w) => (
        <p key={w.code} className="text-[12px]" style={{ color: "var(--p-warn)" }}>
          <b>⚠ {w.label}</b> — {w.detail}
        </p>
      ))}
    </div>
  );
}

/** Where the notetaker is, in words the rep can act on. The waiting room is
 * the one state only a person can fix, so it is said, not spun on. */
function BotStatus({ sessionId }: { sessionId: string }) {
  const [state, setState] = useState<WhisperBotState | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const tick = () =>
      whisperBotState(sessionId)
        .then((s) => live && setState(s))
        .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    void tick();
    const t = setInterval(tick, 4000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [sessionId]);
  if (error) return <div className="border-b px-4 py-2 text-[12px]" style={{ borderColor: "var(--l-rule)", color: "var(--p-bad)" }}>{error}</div>;
  if (!state) return null;
  const listening = state.status === "in_call_recording" || state.status === "recording_permission_allowed";
  return (
    <div className="flex items-center justify-between gap-2 border-b px-4 py-2 text-[12px]"
         style={{ borderColor: "var(--l-rule)", background: state.action ? "var(--p-warn-bg)" : undefined, color: state.action ? "var(--p-warn)" : undefined }}>
      <span className="flex items-center gap-2">
        <Badge tone={listening ? "ok" : state.action ? "warn" : "info"} dot>Notetaker: {state.label}</Badge>
        {state.action && <span>{state.action}</span>}
      </span>
      {!["call_ended", "done", "fatal"].includes(state.status ?? "") && (
        <button onClick={() => void stopWhisperBot(sessionId)} className="underline">remove</button>
      )}
    </div>
  );
}

function Suggestion({ m }: { m: WhisperMessage }) {
  const mine = m.role === "member";
  return (
    <div className="rounded-lg border p-3"
         style={mine ? { marginLeft: 32, background: "var(--l-paper-2)", borderColor: "var(--l-rule)" }
                     : { borderColor: "rgba(180,83,9,.3)", background: "rgba(180,83,9,.04)" }}>
      {m.trigger_text && (
        <div className="mb-2 border-l-2 pl-2 text-[12px] italic p-muted" style={{ borderColor: "var(--l-rule)" }}>
          client asked: {m.trigger_text}
        </div>
      )}
      {/* Markers stripped for display — the evidence they point at is
          listed below as its own row, so the bracket is noise here. */}
      <p className="whitespace-pre-wrap">{stripCitations(m.text)}</p>
      {!mine && <Warnings items={m.warnings} />}
      {!mine && m.evidence?.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {m.evidence.slice(0, 6).map((e) => (
            <span key={e.id} title={e.snippet?.slice(0, 300)} className="p-badge">{e.source_type} · {e.locator.slice(0, 40)}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function WhisperPage() {
  const [sessions, setSessions] = useState<WhisperSession[]>([]);
  const [active, setActive] = useState<WhisperSession | null>(null);
  const [lines, setLines] = useState<WhisperLine[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<WhisperMessage[]>([]);
  const [title, setTitle] = useState("");
  const [meetingUrl, setMeetingUrl] = useState("");
  const [manual, setManual] = useState(false);
  const [draft, setDraft] = useState("");
  const [heard, setHeard] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listWhisperSessions());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);


  const open = useCallback(async (s: WhisperSession) => {
    setActive(s);
    setError(null);
    const thread = await myWhisperThread(s.id);
    setThreadId(thread.id);
    setLines(await listWhisperLines(s.id));
    setMessages(await whisperMessages(thread.id));
  }, []);

  // ?session=<id> — how the call page's "whisper" button lands you straight
  // in the right thread rather than on a list you then have to read.
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || !sessions.length) return;
    const wanted = new URLSearchParams(window.location.search).get("session");
    if (!wanted) return;
    const found = sessions.find((s) => s.id === wanted);
    if (found) {
      opened.current = true;
      void open(found);
    }
  }, [sessions, open]);

  // Poll while a session is open. Suggestions arrive because the CLIENT
  // spoke, not because this tab did anything, so there is nothing to react
  // to locally.
  useEffect(() => {
    if (!active || !threadId) return;
    const t = setInterval(async () => {
      try {
        const [l, m] = await Promise.all([listWhisperLines(active.id), whisperMessages(threadId)]);
        setLines(l);
        setMessages(m);
      } catch {
        /* a dropped poll is not worth surfacing — the next one will tell us */
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [active, threadId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const create = async () => {
    setBusy(true);
    try {
      const s = await createWhisperSession({ title: title.trim() || "Untitled meeting" });
      setTitle("");
      await refreshSessions();
      await open(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  /** Paste a link, get a whisper: the session and the notetaker are created
   *  together, so there is never a session with nothing listening to it. */
  const join = async () => {
    if (!meetingUrl.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const bot = await joinThirdPartyMeeting(meetingUrl.trim(), title.trim() || undefined);
      setMeetingUrl("");
      setTitle("");
      const all = await listWhisperSessions();
      setSessions(all);
      const s = all.find((x) => x.id === bot.session_id);
      if (s) await open(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

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

  /** Paste what the client said. This is the manual stand-in for a capture
   *  source — the same endpoint a meeting bot or a desktop capture posts to,
   *  which is what makes whisper testable with no vendor at all. */
  const feed = async (isClient: boolean) => {
    if (!heard.trim() || !active) return;
    const text = heard.trim();
    setHeard("");
    try {
      await addWhisperLine(active.id, {
        speaker_name: isClient ? "Client" : "Us",
        text,
        is_client: isClient,
      });
      setLines(await listWhisperLines(active.id));
      if (threadId) setMessages(await whisperMessages(threadId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const live = active?.status === "live";
  return (
    <>
      {!active ? (
        <>
          <PageHeader title="Whisper" description="Your agent listens to a meeting and suggests answers only to you. Nobody else sees them." />
          {error && <ErrorNote>{error}</ErrorNote>}
          <div className="mb-5 grid gap-4 lg:grid-cols-3">
            <Card title="Google Meet">
              <p className="p-muted">The Chrome extension reads Meet&apos;s captions and shows suggestions in a side panel. No bot joins, nobody admits anything.</p>
              <Link href="/me?tab=extension" className="p-btn-quiet mt-3">Set up the extension</Link>
            </Card>
            <Card title="Zoom, Teams, or a meeting you're not in">
              <p className="p-muted">A notetaker joins as “your name&apos;s Photon (notes)”, says so in the chat, and never speaks.</p>
              <div className="mt-3 flex gap-2">
                <input className="p-input" placeholder="Paste a meeting link" value={meetingUrl}
                       onChange={(e) => setMeetingUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void join()} />
                <button onClick={() => void join()} disabled={busy || !meetingUrl.trim()} className="p-btn">{busy ? "Sending…" : "Send"}</button>
              </div>
            </Card>
            <Card title="A Photon call">
              <p className="p-muted">Start a call and choose <b>Whispers</b>: everyone&apos;s voice is transcribed and the agent stays silent.</p>
              <Link href="/call" className="p-btn-quiet mt-3">Start a call</Link>
            </Card>
          </div>
          <Card
            title="Sessions"
            actions={<button onClick={() => setManual((m) => !m)} className="p-hint underline">{manual ? "Cancel" : "Test without a meeting"}</button>}
            flush
          >
            {manual && (
              <div className="flex gap-2 border-b px-4 py-3" style={{ borderColor: "var(--l-rule)" }}>
                <input className="p-input max-w-sm" placeholder="Session name" value={title} autoFocus
                       onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void create()} />
                <button onClick={() => void create()} disabled={busy} className="p-btn">Start</button>
                <span className="self-center p-hint">then paste what&apos;s said, as the client or as you</span>
              </div>
            )}
            {!sessions.length ? (
              <Empty title="No whisper sessions yet" />
            ) : (
              <table className="p-table">
                <thead><tr><th>Meeting</th><th>How</th><th>Heard</th><th>Status</th><th>Started</th></tr></thead>
                <tbody>
                  {sessions.map((x) => (
                    <tr key={x.id} className="p-row-hover cursor-pointer" onClick={() => void open(x)}>
                      <td className="font-medium">{x.title || "Untitled"}</td>
                      <td className="p-muted">{sourceOf(x)}</td>
                      <td className="p-muted">{x.line_count} lines</td>
                      <td>{x.status === "live" ? <Badge tone="ok" dot>Live</Badge> : <Badge>Ended</Badge>}</td>
                      <td className="p-hint">{timeAgo(x.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </>
      ) : (
        <>
          <PageHeader
            title={active.title || "Whisper"}
            description={<><button onClick={() => setActive(null)} className="p-link">← All sessions</button> · {sourceOf(active)}</>}
            actions={live ? (
              <button className="p-btn-quiet" onClick={async () => {
                await endWhisperSession(active.id);
                await refreshSessions();
                setActive({ ...active, status: "ended" });
              }}>End session</button>
            ) : <Badge>Ended</Badge>}
          />
          {error && <ErrorNote>{error}</ErrorNote>}
          <div className="grid gap-5 lg:grid-cols-2">
            <section className="p-card flex h-[68vh] flex-col">
              <div className="p-card-h"><span className="p-card-t">In the meeting</span><span className="p-hint">{lines.filter((l) => l.is_client).length} client lines</span></div>
              {active.source === "bot" && live && <BotStatus sessionId={active.id} />}
              <div className="flex-1 space-y-1.5 overflow-y-auto px-4 py-3">
                {lines.map((l) => (
                  <div key={l.id} className={l.is_client ? "" : "p-muted"}>
                    <span className="font-medium" style={l.is_client ? { color: "var(--l-rust)" } : undefined}>{l.speaker_name}:</span> {l.text}
                  </div>
                ))}
                {!lines.length && <p className="p-muted">Nothing captured yet.</p>}
              </div>
              {live && (active.source === "external" || active.source === "photon") && !(active.external_ref ?? "").startsWith("meet:") && (
                <div className="flex gap-2 border-t p-2" style={{ borderColor: "var(--l-rule)" }}>
                  <input className="p-input" placeholder="What was just said…" value={heard}
                         onChange={(e) => setHeard(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void feed(true)} />
                  <button onClick={() => void feed(true)} className="p-btn">Client</button>
                  <button onClick={() => void feed(false)} className="p-btn-quiet">Us</button>
                </div>
              )}
            </section>

            <section className="p-card flex h-[68vh] flex-col" style={{ borderColor: "rgba(180,83,9,.35)" }}>
              <div className="p-card-h"><span className="p-card-t">Your private thread</span><span className="p-hint">only you can see this</span></div>
              <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
                {messages.map((m) => <Suggestion key={m.id} m={m} />)}
                {!messages.length && <p className="p-muted">Suggestions appear here when the client asks something. You can also ask privately.</p>}
                <div ref={bottom} />
              </div>
              <div className="flex gap-2 border-t p-2" style={{ borderColor: "var(--l-rule)" }}>
                <input className="p-input" placeholder="Ask privately…" value={draft}
                       onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void ask()} />
                <button onClick={() => void ask()} disabled={busy} className="p-btn">{busy ? "…" : "Ask"}</button>
              </div>
            </section>
          </div>
        </>
      )}
    </>
  );
}

export default function Page() {
  return (
    <AppShell>
      <WhisperPage />
    </AppShell>
  );
}
