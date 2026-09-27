"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useDataChannel, useLocalParticipant } from "@livekit/components-react";
import { appendChat, CHAT_TOPIC, decodeChat, encodeChat, type ChatMessage } from "@/lib/chat";
import { stripCitations } from "@/lib/evidence";

/** Chat between the humans on the call. Photon does not read it — the panel
 *  next door is where you ask it something. */
export default function ChatPanel() {
  const { localParticipant } = useLocalParticipant();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const onMessage = useCallback(
    (msg: { payload: Uint8Array; from?: { identity: string; name?: string } }) => {
      const body = decodeChat(msg.payload);
      if (!body || !msg.from) return;
      setMessages((prev) =>
        appendChat(prev, {
          id: body.id,
          from: msg.from!.identity,
          name: msg.from!.name || msg.from!.identity,
          text: body.text,
          at: Date.now(),
          local: false,
        }),
      );
    },
    [],
  );

  useDataChannel(CHAT_TOPIC, onMessage);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text) return;
    const id = `${localParticipant.identity}-${Date.now()}`;
    setDraft("");
    // Appended locally because LiveKit does not echo a packet back to its
    // sender — without this your own message never appears.
    setMessages((prev) =>
      appendChat(prev, {
        id,
        from: localParticipant.identity,
        name: "You",
        text,
        at: Date.now(),
        local: true,
      }),
    );
    try {
      await localParticipant.publishData(encodeChat(text, id), {
        reliable: true,
        topic: CHAT_TOPIC,
      });
    } catch {
      /* the message is already on screen; a dropped packet is not worth a modal */
    }
  }, [draft, localParticipant]);

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {messages.length === 0 ? (
          <p className="mt-6 text-[13px] leading-relaxed l-t-2">
            Messages here are between the people on the call. They are not sent to Photon and
            are not part of the meeting transcript.
          </p>
        ) : (
          <ul className="space-y-4">
            {messages.map((m) => (
              <li key={m.id}>
                <div className="flex items-baseline gap-2">
                  <span
                    className="text-[11px] tracking-[0.16em] uppercase"
                    style={{ color: m.local ? "var(--l-rust)" : "var(--l-ink-2)" }}
                  >
                    {m.local ? "You" : m.name}
                  </span>
                  <span className="font-mono text-[10px] l-t-muted">
                    {new Date(m.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </span>
                </div>
                <p className="mt-1 text-[14px] leading-relaxed" style={{ color: "var(--l-ink)" }}>
                  {stripCitations(m.text)}
                </p>
              </li>
            ))}
            <div ref={endRef} />
          </ul>
        )}
      </div>

      <div className="border-t px-5 py-4" style={{ borderColor: "var(--l-rule)" }}>
        <div className="flex gap-2">
          <input
            className="l-input"
            placeholder="Message everyone…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
          />
          <button onClick={send} disabled={!draft.trim()} className="l-btn">
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
