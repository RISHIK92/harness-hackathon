"use client";

import type { Caption } from "@/lib/captions";

/** Live captions the way a call actually wants them: over the video, bottom
 *  centre, only the last couple of lines, never a side panel competing for
 *  attention. The full record lives in the meeting transcript. */
export default function CaptionsOverlay({
  captions,
  visible,
}: {
  captions: Caption[];
  visible: boolean;
}) {
  if (!visible) return null;
  const recent = captions.slice(-2);
  if (recent.length === 0) return null;

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-28 z-20 flex justify-center px-6">
      <div
        className="max-w-2xl rounded-xl px-5 py-3 backdrop-blur-md"
        style={{ background: "rgba(20,20,19,.82)" }}
      >
        {recent.map((c) => (
          <p key={c.id} className="text-[16px] leading-relaxed" style={{ color: "#fffdf8" }}>
            <span
              className="mr-2 text-[11px] tracking-[0.18em] uppercase"
              style={{ color: c.speaker === "agent" ? "var(--l-terra)" : "rgba(255,253,248,.55)" }}
            >
              {c.name}
            </span>
            <span style={{ opacity: c.final ? 1 : 0.75 }}>{c.text}</span>
            {!c.final && <span className="l-caret" />}
          </p>
        ))}
      </div>
    </div>
  );
}
