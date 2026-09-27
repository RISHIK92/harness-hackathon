"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import PhotonWaiting from "../PhotonWaiting";
import PhotonMark from "../../_landing/PhotonMark";
import { getToken, knockForCall, knockStatus, type Knock } from "@/lib/api";

/** Join a call by link: /call/abcd-efgh
 *
 * The code alone does not admit anyone. It gets you to the door, and
 * someone already inside opens it — because a meeting link forwarded one
 * hop too far should not put a stranger into a live customer call.
 *
 * A signed-in workspace member is admitted immediately by the API; they
 * already have access to everything the call can reach, and making
 * colleagues queue teaches people to click Admit without reading it.
 */
export default function JoinBySlug({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const router = useRouter();

  const [name, setName] = useState("");
  const [knock, setKnock] = useState<Knock | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const asked = useRef(false);

  useEffect(() => {
    // Deferred a frame: this decides which screen renders, and setting it
    // straight from the effect body is a cascading render on mount.
    const id = requestAnimationFrame(() => setSignedIn(!!getToken()));
    return () => cancelAnimationFrame(id);
  }, []);

  const enter = useCallback(
    (knockId?: string) => {
      // The call page mints the LiveKit token, and for a guest it needs the
      // admission proof — the token route verifies it server-side.
      const params = new URLSearchParams({ code: slug });
      if (knockId) params.set("knock", knockId);
      if (name.trim()) params.set("name", name.trim());
      router.replace(`/call?${params}`);
    },
    [router, slug, name]
  );

  const ask = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await knockForCall(slug, name.trim());
      setKnock(result);
      if (result.status === "admitted") enter(result.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [slug, name, enter]);

  // A signed-in member should not have to type their name to join their own
  // team's call, so ask on their behalf as soon as we know who they are.
  // Guarded by a ref so a re-render cannot knock twice.
  useEffect(() => {
    if (!signedIn || asked.current) return;
    asked.current = true;
    ask();
  }, [signedIn, ask]);

  // Poll while waiting. Polling rather than a socket: the waiter is not in
  // the room yet, so there is no room connection to listen on.
  useEffect(() => {
    if (!knock || knock.status !== "pending") return;
    const handle = setInterval(async () => {
      try {
        const next = await knockStatus(slug, knock.id);
        if (next.status !== "pending") {
          setKnock(next);
          if (next.status === "admitted") enter(next.id);
        }
      } catch {
        /* transient; keep waiting rather than dumping someone out of the queue */
      }
    }, 2000);
    return () => clearInterval(handle);
  }, [knock, slug, enter]);

  if (knock && knock.status !== "admitted") {
    return (
      <div className="l-landing flex min-h-screen items-center justify-center">
        <PhotonWaiting
          meetingCode={slug}
          name={name || "you"}
          denied={knock.status === "denied"}
        />
      </div>
    );
  }

  return (
    <div className="l-landing relative flex min-h-screen items-center overflow-hidden px-6 md:px-10">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-56 -top-56 h-[640px] w-[640px] rounded-full"
        style={{ border: "1px solid rgba(180,83,9,.10)" }}
      />

      <div className="relative mx-auto w-full max-w-xl">
        <div className="flex items-center gap-4">
          <span className="h-px w-10 shrink-0" style={{ background: "var(--l-rust)" }} />
          <span className="text-[11px] tracking-[0.28em] whitespace-nowrap uppercase l-t-muted">
            Joining a call
          </span>
          <span className="h-px flex-1" style={{ background: "var(--l-rule)" }} />
        </div>

        <PhotonMark fontSize="clamp(52px, 8vw, 92px)" className="mt-7" />

        <p
          className="l-fade mt-8 font-mono text-[clamp(24px,4vw,34px)] tracking-[0.16em]"
          style={{ color: "var(--l-ink)", animationDelay: "760ms" }}
        >
          {slug}
        </p>

        <p
          className="l-fade mt-4 max-w-md text-[15px] leading-relaxed l-t-2"
          style={{ animationDelay: "840ms" }}
        >
          {signedIn
            ? "Checking whether you are already a member of this workspace — if you are, you go straight in."
            : "This code gets you to the door. Someone already in the call opens it, so a link forwarded one hop too far cannot put a stranger into a live customer call."}
        </p>

        {!signedIn && (
          <div className="l-fade mt-10 max-w-sm" style={{ animationDelay: "920ms" }}>
            <label className="l-field block">
              <span
                className="l-field-label block text-[10px] tracking-[0.26em] uppercase"
                style={{ color: "var(--l-muted)" }}
              >
                Your name
              </span>
              <input
                className="mt-2 w-full bg-transparent pb-2 text-[18px] outline-none"
                style={{ color: "var(--l-ink)" }}
                placeholder="How you'll appear to the room"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && name.trim() && ask()}
                autoFocus
              />
              <span className="relative block h-px" style={{ background: "var(--l-rule)" }}>
                <span className="l-field-rule" style={{ background: "var(--l-rust)" }} />
              </span>
            </label>

            <button onClick={ask} disabled={busy || !name.trim()} className="l-btn mt-7 w-full">
              {busy ? "Asking…" : "Ask to join"}
            </button>

            <p className="mt-5 text-[12px] leading-relaxed l-t-muted">
              No account needed. Your name is shown to whoever admits you, and to everyone in
              the room.
            </p>
          </div>
        )}

        {error && (
          <p
            className="l-note mt-8 max-w-sm pl-4 text-[13px] l-t-2"
            style={{ borderLeft: "1px solid var(--l-rust)" }}
          >
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
