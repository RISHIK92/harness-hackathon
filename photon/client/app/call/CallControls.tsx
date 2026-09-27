"use client";

import { useCallback, useEffect, useState } from "react";
import { useLocalParticipant, useParticipants, useRoomContext } from "@livekit/components-react";

const POKE_TOPIC = "photon.poke";
const POKE_WINDOW_SECONDS = 45;

function Icon({ name }: { name: string }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.4, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const paths: Record<string, React.ReactNode> = {
    mic: (
      <>
        <rect x="8" y="3" width="8" height="12" rx="4" />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
      </>
    ),
    micOff: (
      <>
        <rect x="8" y="3" width="8" height="12" rx="4" />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3M4 4l16 16" />
      </>
    ),
    cam: (
      <>
        <rect x="3" y="6" width="12" height="12" rx="2.5" />
        <path d="m15 11 6-3.5v9L15 13z" />
      </>
    ),
    camOff: (
      <>
        <rect x="3" y="6" width="12" height="12" rx="2.5" />
        <path d="m15 11 6-3.5v9L15 13zM4 4l16 16" />
      </>
    ),
    share: (
      <>
        <rect x="2.5" y="4" width="19" height="13" rx="2" />
        <path d="M9 21h6M12 8v5M9.5 10.5 12 8l2.5 2.5" />
      </>
    ),
    more: (
      <>
        <circle cx="12" cy="5" r="1.2" />
        <circle cx="12" cy="12" r="1.2" />
        <circle cx="12" cy="19" r="1.2" />
      </>
    ),
    leave: <path d="M4 7a16 16 0 0 1 16 0v3.5l-4 .8-.6-2.4a12 12 0 0 0-6.8 0l-.6 2.4-4-.8z" />,
  };
  return (
    <svg viewBox="0 0 24 24" width="19" height="19" {...common} aria-hidden>
      {paths[name]}
    </svg>
  );
}

function Round({
  active,
  danger,
  label,
  onClick,
  children,
}: {
  active?: boolean;
  danger?: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className="grid h-11 w-11 place-items-center rounded-full transition-colors"
      style={{
        border: "1px solid",
        borderColor: danger ? "transparent" : active ? "rgba(180,83,9,.5)" : "rgba(255,253,248,.22)",
        background: danger
          ? "var(--l-rust)"
          : active
            ? "rgba(180,83,9,.16)"
            : "rgba(255,253,248,.07)",
        color: danger ? "#fffdf8" : active ? "var(--l-terra)" : "rgba(255,253,248,.9)",
      }}
    >
      {children}
    </button>
  );
}

/**
 * The control bar, over the video rather than beside it. "Ask Photon" sits in
 * the middle as the primary action because on this product it is the reason
 * the call exists — and because a poke is not merely an intent hint: it is
 * what decides whose microphone the agent is listening to.
 */
export default function CallControls({
  captionsOn,
  onToggleCaptions,
  onOpenAdvanced,
  onCopyCode,
  onLeave,
}: {
  captionsOn: boolean;
  onToggleCaptions: () => void;
  onOpenAdvanced: () => void;
  onCopyCode: () => void;
  onLeave: () => void;
}) {
  const room = useRoomContext();
  const participants = useParticipants(); // includes the local participant and the agent
  const { localParticipant, isMicrophoneEnabled, isCameraEnabled, isScreenShareEnabled } =
    useLocalParticipant();

  // A poke exists to say WHOSE microphone the agent should be on. With just
  // one human and the agent there is nothing to disambiguate — LiveKit binds
  // the session to the only human in the room — so the button is noise.
  const needsPoke = participants.length > 2;
  const [menu, setMenu] = useState(false);
  const [pokeUntil, setPokeUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (pokeUntil === null) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [pokeUntil]);

  const remaining = pokeUntil ? Math.max(0, Math.ceil((pokeUntil - now) / 1000)) : 0;
  const listening = remaining > 0;

  const poke = useCallback(async () => {
    try {
      // Empty payload on purpose: the agent reads the sender from the packet,
      // which LiveKit authenticates. Anything in the body is self-asserted.
      await room.localParticipant.publishData(new Uint8Array(), {
        reliable: true,
        topic: POKE_TOPIC,
      });
      setPokeUntil(Date.now() + POKE_WINDOW_SECONDS * 1000);
      setNow(Date.now());
    } catch {
      /* nothing to recover: the agent simply has not been addressed */
    }
  }, [room]);

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 z-30 flex justify-center pb-5">
      <div
        className="pointer-events-auto flex items-center gap-2.5 rounded-full px-3 py-2.5 backdrop-blur-xl"
        style={{ background: "rgba(20,20,19,.78)", border: "1px solid rgba(255,253,248,.12)" }}
      >
        <Round
          label={isMicrophoneEnabled ? "Mute" : "Unmute"}
          active={!isMicrophoneEnabled}
          onClick={() => localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled)}
        >
          <Icon name={isMicrophoneEnabled ? "mic" : "micOff"} />
        </Round>
        <Round
          label={isCameraEnabled ? "Turn camera off" : "Turn camera on"}
          active={!isCameraEnabled}
          onClick={() => localParticipant.setCameraEnabled(!isCameraEnabled)}
        >
          <Icon name={isCameraEnabled ? "cam" : "camOff"} />
        </Round>
        <Round
          label={isScreenShareEnabled ? "Stop sharing" : "Share your screen"}
          active={isScreenShareEnabled}
          onClick={() => localParticipant.setScreenShareEnabled(!isScreenShareEnabled)}
        >
          <Icon name="share" />
        </Round>

        {needsPoke && (
          <button
            onClick={poke}
            className="ml-1 rounded-full px-5 py-2.5 text-[11px] tracking-[0.18em] uppercase whitespace-nowrap transition-colors"
            style={{
              background: listening ? "rgba(180,83,9,.18)" : "#fffdf8",
              color: listening ? "var(--l-terra)" : "#141413",
              border: listening ? "1px solid rgba(180,83,9,.55)" : "1px solid transparent",
            }}
            title="The agent listens to you for the next 45 seconds"
          >
            {listening ? `Listening · ${remaining}s` : "Ask Photon"}
          </button>
        )}

        <button
          onClick={onToggleCaptions}
          title="Captions"
          aria-label="Captions"
          className="grid h-11 w-11 place-items-center rounded-full text-[11px] tracking-[0.1em]"
          style={{
            border: "1px solid",
            borderColor: captionsOn ? "rgba(180,83,9,.5)" : "rgba(255,253,248,.22)",
            background: captionsOn ? "rgba(180,83,9,.16)" : "rgba(255,253,248,.07)",
            color: captionsOn ? "var(--l-terra)" : "rgba(255,253,248,.9)",
          }}
        >
          CC
        </button>

        <div className="relative">
          <Round label="More" onClick={() => setMenu((v) => !v)}>
            <Icon name="more" />
          </Round>
          {menu && (
            <>
              <button
                className="fixed inset-0 z-10 cursor-default"
                aria-label="Close menu"
                onClick={() => setMenu(false)}
              />
              <div className="l-sheet absolute bottom-14 right-0 z-20 w-60 p-2">
                {[
                  ["Pipeline trace", () => { setMenu(false); onOpenAdvanced(); }],
                  ["Copy meeting code", () => { setMenu(false); onCopyCode(); }],
                ].map(([label, fn]) => (
                  <button
                    key={label as string}
                    onClick={fn as () => void}
                    className="w-full rounded-lg px-3 py-2 text-left text-[13px] transition-colors hover:bg-[rgba(28,25,23,.04)]"
                    style={{ color: "var(--l-ink-2)" }}
                  >
                    {label as string}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        <Round label="Leave the call" danger onClick={onLeave}>
          <Icon name="leave" />
        </Round>
      </div>
    </div>
  );
}
