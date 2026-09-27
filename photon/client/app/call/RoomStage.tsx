"use client";

import { GridLayout, ParticipantTile, useTracks } from "@livekit/components-react";
import { Track } from "livekit-client";

/**
 * The stage. One participant sharing their screen takes the room over — that
 * is what everyone is looking at — and the faces drop to a strip beside it.
 * Otherwise it is an even grid, which is what a four-person call wants.
 */
export default function RoomStage() {
  const tracks = useTracks(
    [
      { source: Track.Source.Camera, withPlaceholder: true },
      { source: Track.Source.ScreenShare, withPlaceholder: false },
    ],
    { onlySubscribed: false },
  );

  const screen = tracks.find((t) => t.publication?.source === Track.Source.ScreenShare);
  const cameras = tracks.filter((t) => t.publication?.source !== Track.Source.ScreenShare);

  if (screen) {
    return (
      <div className="flex h-full gap-3 p-3">
        <div className="min-w-0 flex-1 overflow-hidden rounded-xl" style={{ background: "#141413" }}>
          <ParticipantTile trackRef={screen} style={{ height: "100%" }} />
        </div>
        <div className="hidden w-52 shrink-0 flex-col gap-3 overflow-y-auto md:flex">
          {cameras.map((t) => (
            <div
              key={`${t.participant.identity}-${t.publication?.trackSid ?? "cam"}`}
              className="aspect-video shrink-0 overflow-hidden rounded-xl"
              style={{ background: "#141413" }}
            >
              <ParticipantTile trackRef={t} style={{ height: "100%" }} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full p-3">
      <GridLayout tracks={cameras} style={{ height: "100%" }}>
        <ParticipantTile />
      </GridLayout>
    </div>
  );
}
