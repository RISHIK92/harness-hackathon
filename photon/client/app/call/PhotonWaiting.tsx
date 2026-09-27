"use client";

/** The waiting state, in the landing page's visual language.
 *
 * A spinner would say "something is happening". This has to say something
 * more specific: a person is being asked, and the wait ends when they
 * answer — not on a timer. So the photon arrives, and arrives again, rather
 * than spinning; and the copy names who is deciding.
 */
export default function PhotonWaiting({
  meetingCode,
  name,
  denied,
}: {
  meetingCode: string;
  name: string;
  denied?: boolean;
}) {
  return (
    <div className="w-full max-w-md px-6">
      <div className="flex items-center gap-4">
        <span className="h-px w-10 shrink-0" style={{ background: "var(--l-rust)" }} />
        <span className="text-[11px] tracking-[0.28em] whitespace-nowrap uppercase l-t-muted">
          {denied ? "Not admitted" : "At the door"}
        </span>
        <span className="h-px flex-1" style={{ background: "var(--l-rule)" }} />
      </div>

      <div className="relative mt-10 h-14 w-full overflow-hidden">
        {!denied && (
          <>
            <span className="l-photon-loop" />
            <span className="l-ring-loop" />
          </>
        )}
      </div>

      {denied ? (
        <>
          <h1 className="text-[clamp(26px,3.4vw,36px)] leading-[1.15]" style={{ color: "var(--l-ink)" }}>
            Someone{" "}
            <span style={{ fontFamily: "var(--font-display)", fontStyle: "italic" }}>
              declined
            </span>{" "}
            this request.
          </h1>
          <p className="mt-5 text-[15px] leading-relaxed l-t-2">
            If that was not expected, ask them directly — requesting again will not change it.
          </p>
        </>
      ) : (
        <>
          <h1 className="text-[clamp(26px,3.4vw,36px)] leading-[1.15]" style={{ color: "var(--l-ink)" }}>
            Waiting to be{" "}
            <span style={{ fontFamily: "var(--font-display)", fontStyle: "italic" }}>let in</span>.
          </h1>
          <p className="mt-5 text-[15px] leading-relaxed l-t-2">
            Someone already in{" "}
            <span className="font-mono tracking-[0.1em]" style={{ color: "var(--l-ink)" }}>
              {meetingCode}
            </span>{" "}
            has to admit you. They will see you as{" "}
            <span style={{ color: "var(--l-ink)" }}>{name}</span>.
          </p>
          <p className="mt-6 flex items-center gap-3 text-[12px] l-t-muted">
            <span className="l-dot l-dot-live" />
            Keep this open — you join the moment they do.
          </p>
        </>
      )}
    </div>
  );
}
