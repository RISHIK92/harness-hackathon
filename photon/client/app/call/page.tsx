"use client";

import Link from "next/link";

import "@livekit/components-styles";
import { useCallback, useEffect, useRef, useState } from "react";
import { LiveKitRoom, RoomAudioRenderer } from "@livekit/components-react";
import { AgentAnswer } from "@/lib/evidence";
import { getCallConfig, getWorkspaceId } from "@/lib/api";
import CaptionsBridge from "./CaptionsBridge";
import CaptionsOverlay from "./CaptionsOverlay";
import CallSetup from "./CallSetup";
import CallControls from "./CallControls";
import ConnectSourcePrompt from "./ConnectSourcePrompt";
import RoomStage from "./RoomStage";
import AskPanel from "./AskPanel";
import ChatPanel from "./ChatPanel";
import CodePanel from "./CodePanel";
import WhisperPanel from "./WhisperPanel";
import AgentPresence from "./AgentPresence";
import TraceBridge from "./TraceBridge";
import AdvancedPanel from "./AdvancedPanel";
import { mergeCaption, type Caption } from "@/lib/captions";
import { applyTraceEvent, type TraceEvent, type TurnTrace } from "@/lib/trace";
import { createConfiguredMeeting, getToken } from "@/lib/api";
import WaitingRoom from "./WaitingRoom";
import { formatMeetingCode } from "@/lib/meeting";

const BRAIN_API_URL = process.env.NEXT_PUBLIC_BRAIN_API_URL || "http://localhost:8000";

type ConnState = "idle" | "connecting" | "connected" | "error";
type Turn = { role: "user" | "agent"; question?: string; result?: AgentAnswer };

// Matches call-agent/orchestrator.py's HISTORY_TURNS. Three exchanges is
// enough for the follow-up chains people actually type, and every extra
// turn is input tokens on the critical path of the answer.
const HISTORY_TURNS = 6;
// The trace tab is not in the strip: it is reached from the overflow menu, so
// a live support call is not fronted by a debugging view.
type Tab = "ask" | "chat" | "code" | "whisper" | "trace";
const TABS: { key: Tab; label: string }[] = [
  { key: "ask", label: "Ask" },
  { key: "chat", label: "Chat" },
  { key: "code", label: "Code" },
  { key: "whisper", label: "Whisper" },
];
type TokenData = { url: string; token: string };

export default function CallPage() {
  const [identity, setIdentity] = useState("");
  // The meeting code (abcd-efgh) doubles as the LiveKit room name. Signed-in
  // users create one; anyone with the code — including external guests who
  // are not workspace members — can join it.
  const [meetingCode, setMeetingCode] = useState("");
  // Which source the user asked to connect, if any. Held here rather than
  // inside CallSetup because accepting navigates away from the call.
  const [connectPrompt, setConnectPrompt] = useState<string | null>(null);
  // Admission proof from /call/{slug}: the code alone gets a guest to the
  // door, and someone inside opens it. The token route verifies this
  // server-side, so it is passed through rather than trusted here.
  const [knockId, setKnockId] = useState<string | null>(null);
  const [autoJoin, setAutoJoin] = useState(false);
  const autoJoined = useRef(false);

  // Whether someone is signed in can only be known on the client
  // (localStorage), so it starts false and is set after mount. Reading it
  // directly during render made the server and client disagree — a real
  // hydration mismatch, not a warning to silence.
  const [signedIn, setSignedIn] = useState(false);

  // Returning from "Connect now" lands here with the original code, so the
  // user rejoins the same meeting instead of losing the one they set up.
  useEffect(() => {
    // Deferred a frame rather than set from the effect body: both of these
    // change what the lobby renders, and a cascading render on mount is what
    // the rule is warning about.
    const id = requestAnimationFrame(() => {
      const authed = !!getToken();
      setSignedIn(authed);
      const params = new URLSearchParams(window.location.search);
      const code = params.get("code");
      const knock = params.get("knock");
      const name = params.get("name");
      if (code) setMeetingCode(code);
      if (knock) setKnockId(knock);
      if (name) setIdentity(name);
      // Arriving from the join link with the door already open — asking for
      // the code and the name again would be asking twice.
      if (code && (knock || authed)) setAutoJoin(true);
    });
    return () => cancelAnimationFrame(id);
  }, []);
  const [state, setState] = useState<ConnState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [tokenData, setTokenData] = useState<TokenData | null>(null);
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [traces, setTraces] = useState<TurnTrace[]>([]);
  const [textInput, setTextInput] = useState("");
  const [textBusy, setTextBusy] = useState(false);
  // The side panel is an overlay, so it has to be closable — the video is
  // the page, everything else is on demand.
  const [panel, setPanel] = useState<Tab | null>("ask");
  // Reopening the drawer should return you to what you were reading.
  const [lastTab, setLastTab] = useState<Tab>("ask");
  const [captionsOn, setCaptionsOn] = useState(true);
  const [copied, setCopied] = useState(false);

  const connect = useCallback(
    async (config?: {
      bot_types: string[];
      language_mode: string;
      enabled_sources: string[];
      attends_as?: "member" | "company";
      mode?: "speak" | "whisper";
    }) => {
      setState("connecting");
      setError(null);
      try {
        const token = getToken();
        let code = meetingCode.trim().toLowerCase();

        if (!code) {
          if (!token) {
            setError("Enter a meeting code to join, or sign in to start one.");
            setState("idle");
            return;
          }
          // A new call carries the configuration chosen on the setup screen;
          // joining an existing code inherits whatever that call was set up
          // with, which is why config is only sent when creating.
          code = (
            await createConfiguredMeeting({
              bot_types: config?.bot_types ?? ["support"],
              language_mode: config?.language_mode ?? "english",
              enabled_sources: config?.enabled_sources ?? [],
              attends_as: config?.attends_as ?? "member",
              mode: config?.mode ?? "speak",
            })
          ).slug;
          setMeetingCode(code);
        }

        const params = new URLSearchParams({ room: code });
        if (knockId) params.set("knock", knockId);
        if (!token) {
          if (!identity.trim()) {
            setError("Enter your name to join as a guest.");
            setState("idle");
            return;
          }
          params.set("name", identity.trim());
        }

        const res = await fetch(`/api/livekit-token?${params}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "failed to get a token");
        setTokenData({ url: data.url, token: data.token });
        setState("connected");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setState("error");
      }
    },
    [identity, meetingCode, knockId]
  );

  const onDisconnected = useCallback(() => {
    setState("idle");
    setTokenData(null);
  }, []);

  const onCaption = useCallback((caption: Caption) => {
    setCaptions((prev) => mergeCaption(prev, caption));
  }, []);

  // One reducer for both feeds: voice events arrive over the LiveKit data
  // channel (TraceBridge), text events over SSE from askByText below.
  const onTraceEvent = useCallback((event: TraceEvent, fallbackTurnId = "voice") => {
    setTraces((prev) => applyTraceEvent(prev, event, fallbackTurnId));
  }, []);

  // A voice turn's question and its answer arrive as two separate data
  // packets, so the question is parked by turn_id until its answer lands.
  // Appending on "turn.requested" instead would leave a dangling empty
  // agent bubble for every turn that never produces one (ambient speech).
  const voiceQuestions = useRef(new Map<string, string>());

  const onVoiceTraceEvent = useCallback(
    (event: TraceEvent) => {
      onTraceEvent(event, "voice");

      // Voice answers reach the browser already — the orchestrator
      // republishes the brain-api's whole SSE stream over the data channel,
      // and "turn.done" carries the full structured result. Only the
      // advanced panel was reading it, so the evidence and code panels sat
      // empty for the entire call while a voice conversation happened in
      // front of them. Everything below is just folding that same event
      // into the turn list the text path already builds.
      const turnId = String(event.turn_id ?? "voice");

      if (event.type === "turn.requested" && typeof event.question === "string") {
        voiceQuestions.current.set(turnId, event.question);
        return;
      }

      if (event.type !== "turn.done" || !event.result) return;
      const result = event.result as AgentAnswer;

      // The small-talk fast path publishes a turn.done too, so the panel
      // can show a deliberate 0ms path. It ran no tools and cited nothing,
      // so it belongs in the captions, not in the record of what answers
      // were grounded in — folding it in would put "(no reply — ambient
      // speech)" in the evidence panel.
      if (!result.tool_trace?.length) {
        voiceQuestions.current.delete(turnId);
        return;
      }

      const question = voiceQuestions.current.get(turnId);
      voiceQuestions.current.delete(turnId);
      setTurns((prev) => [
        ...prev,
        ...(question ? [{ role: "user" as const, question }] : []),
        { role: "agent" as const, result },
      ]);
    },
    [onTraceEvent]
  );

  const askByText = useCallback(async () => {
    const question = textInput.trim();
    if (!question || textBusy) return;
    setTextInput("");
    // Read from the turns rendered on screen, before this question joins
    // them — history means the turns BEFORE this one. Both the text turns
    // and the voice ones are in here, so a question typed after a spoken
    // exchange still resolves against what was said aloud.
    const history = turns
      .map((t) =>
        t.role === "user"
          ? { role: "user", text: t.question ?? "" }
          : { role: "agent", text: t.result?.answer ?? "" }
      )
      .filter((t) => t.text)
      .slice(-HISTORY_TURNS);
    setTurns((prev) => [...prev, { role: "user", question }, { role: "agent" }]);
    setTextBusy(true);

    const turnId = `text-${Date.now()}`;
    const fail = (message: string) => {
      setTurns((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          role: "agent",
          result: {
            answer: message,
            claims: [],
            confidence: "low",
            abstained: true,
            escalation: null,
            tool_trace: [],
          },
        };
        return next;
      });
    };

    try {
      // The event stream, not the plain POST: same answer, but the
      // advanced panel gets each plan/tool/compose step as it happens
      // instead of a single silent wait of tens of seconds.
      // Best-effort: this join page isn't behind login, but if this
      // browser also has a workspace selected from the dashboard, use it
      // so the agent can disambiguate across that workspace's repos
      // instead of falling back to the single seed repo. See CLAUDE.md's
      // multi-repo disambiguation note — the agent endpoint itself is
      // still unauthenticated, so this is client-asserted, not verified.
      const workspace_id = getWorkspaceId() || undefined;
      // The meeting, when we're in one, outranks the client-asserted
      // workspace: the server resolves the call's configuration from the
      // slug (persona, and which sources are allowed), and a call that was
      // set up to exclude a source must not have it reintroduced by
      // whatever workspace this browser happens to have selected.
      const meeting_slug = meetingCode.trim() || undefined;
      const res = await fetch(`${BRAIN_API_URL}/api/agent/ask/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "ngrok-skip-browser-warning": "1" },
        body: JSON.stringify({ question, workspace_id, meeting_slug, history }),
      });
      if (!res.ok || !res.body) throw new Error(`brain-api returned ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let result: AgentAnswer | null = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE frames are separated by a blank line; a chunk can split one
        // in half, so keep the trailing partial frame in the buffer.
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const event = JSON.parse(line.slice(6)) as TraceEvent;
          onTraceEvent(event, turnId);
          if (event.type === "turn.done") result = event.result as AgentAnswer;
          if (event.type === "turn.error") throw new Error(String(event.error));
        }
      }

      if (!result) throw new Error("the stream ended before the answer arrived");
      const answer = result;
      setTurns((prev) => {
        const next = [...prev];
        next[next.length - 1] = { role: "agent", result: answer };
        return next;
      });
    } catch (e) {
      fail(`Error reaching the agent: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTextBusy(false);
    }
  }, [textInput, textBusy, onTraceEvent, meetingCode, turns]);

  // Fires once: connect() is async, so this never updates state
  // synchronously from the effect body.
  useEffect(() => {
    if (!autoJoin || autoJoined.current || !meetingCode) return;
    autoJoined.current = true;
    connect();
  }, [autoJoin, meetingCode, connect]);

  const openTab = useCallback((tab: Tab) => {
    setLastTab(tab);
    setPanel((prev) => (prev === tab ? null : tab));
  }, []);

  const [whisperSession, setWhisperSession] = useState<string | null>(null);
  // A whisper-mode call: the agent is only listening. Known to everyone in
  // the room, guests included — they are the people being transcribed.
  const [callMode, setCallMode] = useState<{ mode: "speak" | "whisper"; name: string } | null>(null);
  useEffect(() => {
    if (state !== "connected" || !meetingCode) return;
    getCallConfig(meetingCode)
      .then((c) => {
        setCallMode({ mode: c.mode, name: c.display_name });
        // A member on a whisper call is here for the suggestions; open them.
        if (c.mode === "whisper" && getToken()) setPanel("whisper");
      })
      .catch(() => setCallMode(null));
  }, [state, meetingCode]);
  // Lines reach whisper through ONE path: the worker's transcript, fed to the
  // meeting's session server-side, where who is a member is known from a
  // signed identity. The browser used to forward captions too, and every
  // question was ingested — and answered — twice.

  const copyCode = useCallback(() => {
    navigator.clipboard?.writeText(meetingCode);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }, [meetingCode]);

  const inRoom = state === "connected" && tokenData;

  return (
    <div className="l-landing flex h-screen flex-col overflow-hidden">
      {connectPrompt && (
        <ConnectSourcePrompt
          sourceLabel={connectPrompt.replace(/_/g, " ")}
          inCall={state === "connected"}
          onAccept={() => {
            // Carry the meeting code through the detour so the user comes
            // back to THIS call rather than starting a fresh one.
            const back = meetingCode ? `/call?code=${meetingCode}` : "/call";
            window.location.href =
              `/dashboard?connect=${connectPrompt}&return=${encodeURIComponent(back)}`;
          }}
          onLater={() => setConnectPrompt(null)}
        />
      )}

      {!inRoom ? (
        <Lobby
          signedIn={signedIn}
          state={state}
          error={error}
          identity={identity}
          meetingCode={meetingCode}
          onIdentity={setIdentity}
          onMeetingCode={setMeetingCode}
          onConnectSource={(key) => setConnectPrompt(key)}
          onStart={connect}
        />
      ) : (
        <LiveKitRoom
          serverUrl={tokenData.url}
          token={tokenData.token}
          connect
          audio
          video={false}
          data-lk-theme="default"
          onDisconnected={onDisconnected}
          className="flex min-h-0 flex-1 flex-col"
        >
          <RoomAudioRenderer />

          <header
            className="flex shrink-0 items-center gap-4 border-b px-6 py-3"
            style={{ borderColor: "rgba(255,253,248,.10)", background: "var(--l-night)" }}
          >
            <span
              className="text-[22px] leading-none italic"
              style={{ fontFamily: "var(--font-display)", color: "var(--l-terra)" }}
            >
              photon
            </span>
            <span className="h-4 w-px" style={{ background: "rgba(255,253,248,.16)" }} />
            <button
              onClick={copyCode}
              className="font-mono text-[13px] tracking-[0.12em] transition-opacity hover:opacity-70"
              style={{ color: "var(--l-terra)" }}
              title="Copy the meeting code"
            >
              {copied ? "copied" : meetingCode}
            </button>
            <div className="flex-1" />
            {/* Whisper on this call. Only offered to a signed-in member — a
                guest has no workspace to whisper in, and the server would
                refuse anyway. Opens the private thread in a new tab so it
                does not take the call's own tab with it. */}
            {getToken() && meetingCode && (
              <button
                onClick={() => setPanel("whisper")}
                className="text-[12px] tracking-[0.08em] transition-opacity hover:opacity-70 disabled:opacity-40"
                style={{ color: "var(--l-terra)" }}
                title="Photon suggests answers privately, only to you"
              >
                whisper
              </button>
            )}
          </header>

          <AgentPresence room={meetingCode} agentLabel={callMode?.name ?? "Photon"} />

          {callMode?.mode === "whisper" && (
            <div
              className="shrink-0 px-6 py-1.5 text-[12px] tracking-[0.04em]"
              style={{ background: "rgba(180,83,9,.14)", color: "var(--l-terra)" }}
            >
              {callMode.name} is taking notes on this call. It won&apos;t speak — it only listens.
            </div>
          )}

          <div className="flex min-h-0 flex-1">
            {/* the stage: video is the page, and the panel slides over it */}
            <div className="relative min-w-0 flex-1" style={{ background: "#141413" }}>
              <RoomStage />
              <CaptionsOverlay captions={captions} visible={captionsOn} />
              <CallControls
                captionsOn={captionsOn}
                onToggleCaptions={() => setCaptionsOn((v) => !v)}
                onOpenAdvanced={() => setPanel("trace")}
                onCopyCode={copyCode}
                onLeave={onDisconnected}
              />
              {signedIn && <WaitingRoom slug={meetingCode} />}
              {panel === null && (
                <button
                  onClick={() => setPanel(lastTab)}
                  className="absolute right-0 top-1/2 z-30 -translate-y-1/2 rounded-l-xl px-2.5 py-5"
                  style={{
                    background: "var(--l-paper)",
                    border: "1px solid var(--l-rule)",
                    borderRight: "none",
                  }}
                  aria-label="Open the panel"
                >
                  <span
                    className="block text-[10px] tracking-[0.22em] uppercase"
                    style={{
                      writingMode: "vertical-rl",
                      transform: "rotate(180deg)",
                      color: "var(--l-rust)",
                    }}
                  >
                    {TABS.find((t) => t.key === lastTab)?.label ?? "Ask"}
                  </span>
                </button>
              )}
              <CaptionsBridge onCaption={onCaption} />
              <TraceBridge onEvent={onVoiceTraceEvent} />
            </div>

            <aside
              className="shrink-0 overflow-hidden border-l transition-[width] duration-500"
              style={{
                width: panel ? "min(30rem, 40vw)" : 0,
                borderColor: panel ? "var(--l-rule)" : "transparent",
                transitionTimingFunction: "cubic-bezier(.16,1,.3,1)",
                background: "var(--l-paper)",
              }}
            >
              <div className="flex h-full w-[min(30rem,40vw)] flex-col">
                <div
                  className="flex shrink-0 items-center gap-1 border-b px-4 py-3"
                  style={{ borderColor: "var(--l-rule)" }}
                >
                  {[...TABS, ...(panel === "trace" ? [{ key: "trace" as Tab, label: "Trace" }] : [])].map(
                    (t) => (
                      <button
                        key={t.key}
                        onClick={() => {
                          setLastTab(t.key);
                          setPanel(t.key);
                        }}
                        className="rounded-full px-3.5 py-1.5 text-[11px] tracking-[0.16em] uppercase transition-colors"
                        style={{
                          background: panel === t.key ? "rgba(180,83,9,.10)" : "transparent",
                          color: panel === t.key ? "var(--l-rust)" : "var(--l-ink-2)",
                        }}
                      >
                        {t.label}
                      </button>
                    ),
                  )}
                  <div className="flex-1" />
                  <button
                    onClick={() => setPanel(null)}
                    aria-label="Close panel"
                    className="text-[16px] leading-none l-quiet"
                  >
                    ×
                  </button>
                </div>

                <div className="min-h-0 flex-1">
                  {panel === "ask" && (
                    <AskPanel
                      turns={turns}
                      value={textInput}
                      busy={textBusy}
                      onChange={setTextInput}
                      onAsk={askByText}
                    />
                  )}
                  {panel === "chat" && <ChatPanel />}
                  {panel === "code" && <CodePanel turns={turns} />}
                  {panel === "whisper" && (
                    <WhisperPanel
                      meetingCode={meetingCode}
                      sessionId={whisperSession}
                      onSession={setWhisperSession}
                    />
                  )}
                  {panel === "trace" && (
                    <div className="h-full overflow-y-auto px-5 py-4">
                      <AdvancedPanel turns={traces} />
                    </div>
                  )}
                </div>
              </div>
            </aside>
          </div>
        </LiveKitRoom>
      )}
    </div>
  );
}

/** Before the call: one screen that either starts a call or joins one. */
function Lobby({
  signedIn,
  state,
  error,
  identity,
  meetingCode,
  onIdentity,
  onMeetingCode,
  onConnectSource,
  onStart,
}: {
  signedIn: boolean;
  state: ConnState;
  error: string | null;
  identity: string;
  meetingCode: string;
  onIdentity: (v: string) => void;
  onMeetingCode: (v: string) => void;
  onConnectSource: (key: string) => void;
  onStart: (config?: {
    bot_types: string[];
    language_mode: string;
    enabled_sources: string[];
  }) => void;
}) {
  const joining = meetingCode.trim().length > 0;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto grid max-w-6xl gap-14 px-6 py-14 md:grid-cols-[1fr_26rem] md:px-10">
        <div>
          {/* The call is a full-screen room outside the app frame; before
              joining, there has to be a way back into the app. */}
          {signedIn && (
            <Link href="/home" className="mb-6 inline-block text-[12px] tracking-[0.08em] l-quiet">
              ← Back to Photon
            </Link>
          )}
          <div className="flex items-center gap-4">
            <span className="h-px w-10" style={{ background: "var(--l-rust)" }} />
            <span className="text-[11px] tracking-[0.28em] uppercase l-t-muted">
              {signedIn ? "Start or join a call" : "Join a call"}
            </span>
            <span className="h-px flex-1" style={{ background: "var(--l-rule)" }} />
          </div>

          <h1
            className="mt-8 text-[clamp(30px,4vw,48px)] leading-[1.1]"
            style={{ color: "var(--l-ink)" }}
          >
            Bring it{" "}
            <span style={{ fontFamily: "var(--font-display)", fontStyle: "italic" }}>
              onto the call
            </span>
            .
          </h1>
          <p className="mt-5 max-w-md text-[15px] leading-relaxed l-t-2">
            Press <span style={{ color: "var(--l-ink)" }}>Ask Photon</span> and it listens to
            you — and only you — for forty-five seconds. Side conversation stays private, and
            the answer comes back cited, in the language you asked in.
          </p>

          {signedIn && !joining && (
            <div className="mt-12">
              <CallSetup
                busy={state === "connecting"}
                onConnectSource={onConnectSource}
                onStart={onStart}
              />
            </div>
          )}
        </div>

        {/* flat, not a raised sheet — the rest of this design is hairlines on
            paper, and a drop-shadowed card reads as a different product */}
        <div className="h-fit border-t pt-6" style={{ borderColor: "var(--l-rust)" }}>
          <p className="text-[10px] tracking-[0.26em] uppercase l-t-muted">
            {signedIn ? "Or join with a code" : "Meeting code"}
          </p>
          <input
            className="l-input mt-4 font-mono tracking-[0.12em]"
            placeholder="abcd-efgh"
            value={meetingCode}
            maxLength={9}
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            inputMode="text"
            onChange={(e) => onMeetingCode(formatMeetingCode(e.target.value))}
          />
          {!signedIn && (
            <input
              className="l-input mt-3"
              placeholder="Your name"
              value={identity}
              onChange={(e) => onIdentity(e.target.value)}
            />
          )}
          <button
            onClick={() => onStart()}
            disabled={state === "connecting" || !meetingCode.trim()}
            className="l-btn mt-5 w-full"
          >
            {state === "connecting" ? "Connecting…" : "Join with code"}
          </button>
          {error && (
            <p
              className="l-note mt-5 pl-4 text-[13px] l-t-2"
              style={{ borderLeft: "1px solid var(--l-rust)" }}
            >
              {error}
            </p>
          )}
          <p className="mt-6 text-[12px] leading-relaxed l-t-muted">
            A code has no 0, O, 1, l or I in it — the first thing anyone does with one is read
            it aloud. Guests do not need an account.
          </p>
        </div>
      </div>
    </div>
  );
}
