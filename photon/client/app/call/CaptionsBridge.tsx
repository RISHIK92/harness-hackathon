"use client";

import { useEffect } from "react";
import { ParticipantKind, RoomEvent, type Participant, type TranscriptionSegment } from "livekit-client";
import { useRoomContext } from "@livekit/components-react";
import type { Caption } from "@/lib/captions";

/** Must render inside <LiveKitRoom> — useRoomContext() only resolves there.
 * Forwards STT captions out to the page so they can sit outside the
 * VideoConference prefab instead of fighting its own layout for space.
 *
 * LiveKit publishes transcriptions for BOTH sides on the same event: the
 * human's STT (attributed to the speaking participant) and the agent's own
 * TTS (attributed to the agent participant). They're only distinguishable
 * by who the segment came from, so that attribution is resolved here and
 * carried on every caption rather than being thrown away. */
export default function CaptionsBridge({ onCaption }: { onCaption: (c: Caption) => void }) {
  const room = useRoomContext();

  useEffect(() => {
    const handler = (segments: TranscriptionSegment[], participant?: Participant) => {
      // No participant means the segment came from the agent's own speech
      // synthesis rather than a subscribed audio track.
      const isAgent =
        !participant ||
        participant.kind === ParticipantKind.AGENT ||
        participant.identity.startsWith("agent-");
      const isLocal = !!participant && participant.identity === room.localParticipant.identity;

      for (const s of segments) {
        if (!s.text) continue;
        onCaption({
          id: s.id,
          speaker: isAgent ? "agent" : "user",
          name: isAgent ? "Photon" : isLocal ? "You" : participant!.name || participant!.identity,
          isLocal,
          text: s.text,
          final: s.final,
          at: s.firstReceivedTime || Date.now(),
        });
      }
    };
    room.on(RoomEvent.TranscriptionReceived, handler);
    return () => {
      room.off(RoomEvent.TranscriptionReceived, handler);
    };
  }, [room, onCaption]);

  return null;
}
