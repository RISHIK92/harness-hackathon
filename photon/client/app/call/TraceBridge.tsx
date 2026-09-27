"use client";

import { useEffect } from "react";
import { RoomEvent } from "livekit-client";
import { useRoomContext } from "@livekit/components-react";
import type { TraceEvent } from "@/lib/trace";

/** The voice half of the advanced panel's feed. call-agent's orchestrator
 * streams each turn from the brain-api and republishes every trace event
 * over LiveKit's data channel on the "photon.trace" topic
 * (call-agent/adapters/livekit_adapter.py). Must render inside
 * <LiveKitRoom>, same as CaptionsBridge. */
const TRACE_TOPIC = "photon.trace";

export default function TraceBridge({ onEvent }: { onEvent: (e: TraceEvent) => void }) {
  const room = useRoomContext();

  useEffect(() => {
    const decoder = new TextDecoder();
    const handler = (payload: Uint8Array, _p: unknown, _k: unknown, topic?: string) => {
      if (topic !== TRACE_TOPIC) return; // never confuse chat/other data traffic for a trace
      try {
        onEvent(JSON.parse(decoder.decode(payload)) as TraceEvent);
      } catch {
        // A malformed packet is not worth breaking the panel over.
      }
    };
    room.on(RoomEvent.DataReceived, handler);
    return () => {
      room.off(RoomEvent.DataReceived, handler);
    };
  }, [room, onEvent]);

  return null;
}
