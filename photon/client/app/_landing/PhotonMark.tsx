"use client";

/**
 * The wordmark and its arrival. A single particle crosses in from the left as
 * a streak, collapses to a point at the head of the mark, and the letters
 * unfold out of that point — each splitting into a warm and a cool ghost that
 * recombine as it settles.
 *
 * Shared by the hero and the login screen so the entrance is one definition,
 * not two that drift apart.
 */

/** Every letter starts pulled back toward the impact point — further back the
 *  later it sits — with a little vertical jitter so it reads as light. */
const MARK = "photon".split("").map((c, i) => ({
  c,
  dx: -(38 + i * 34),
  dy: [7, -9, 5, -6, 9, -4][i],
}));

const LAND_MS = 420; // when the photon arrives and the word starts existing

export const MARK_SETTLED_MS = LAND_MS + (MARK.length - 1) * 62 + 950;

export default function PhotonMark({
  as: Tag = "div",
  fontSize,
  delay = 0,
  className = "",
}: {
  as?: "h1" | "div";
  fontSize: string;
  /** shifts the whole arrival later, for screens where it is not the first beat */
  delay?: number;
  className?: string;
}) {
  return (
    <div className={`relative ${className}`}>
      <span
        aria-hidden
        className="l-photon"
        style={delay ? { animationDelay: `${delay}ms` } : undefined}
      />
      <span
        aria-hidden
        className="l-ring"
        style={{ animationDelay: `${400 + delay}ms` }}
      />
      <Tag
        className="relative flex italic"
        style={{
          fontFamily: "var(--font-display)",
          fontSize,
          lineHeight: 0.9,
          color: "var(--l-ink)",
          letterSpacing: "-0.015em",
        }}
      >
        {MARK.map((m, i) => (
          <span
            key={i}
            className="l-letter"
            style={
              {
                "--dx": `${m.dx}px`,
                "--dy": `${m.dy}px`,
                animationDelay: `${LAND_MS + delay + i * 62}ms`,
              } as React.CSSProperties
            }
          >
            {m.c}
          </span>
        ))}
      </Tag>
    </div>
  );
}
