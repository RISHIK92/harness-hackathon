"use client";

import { useCallback, useEffect, useState } from "react";
import { decideKnock, listWaiting, type WaitingPerson } from "@/lib/api";

/** Who is at the door.
 *
 * The knock flow puts guests in a queue; without this nobody can open it.
 * Shown only to signed-in members — the API rejects a decision from anyone
 * else anyway, so a guest seeing this would be a button that cannot work.
 *
 * Polled rather than pushed: admission happens over HTTP, not the room's
 * data channel, so there is no event to subscribe to. Four seconds is slow
 * enough to be cheap and fast enough that nobody is left standing outside.
 */
export default function WaitingRoom({ slug }: { slug: string }) {
  const [waiting, setWaiting] = useState<WaitingPerson[]>([]);

  const refresh = useCallback(async () => {
    try {
      setWaiting(await listWaiting(slug));
    } catch {
      /* not a member of this workspace, or the meeting is gone — stay quiet */
    }
  }, [slug]);

  useEffect(() => {
    if (!slug) return;
    const t = setInterval(refresh, 4000);
    // First poll on the next frame rather than in the effect body — refresh()
    // sets state, and doing that synchronously on mount is a cascading render.
    const first = requestAnimationFrame(refresh);
    return () => {
      clearInterval(t);
      cancelAnimationFrame(first);
    };
  }, [slug, refresh]);

  const decide = async (id: string, admit: boolean) => {
    setWaiting((prev) => prev.filter((p) => p.id !== id)); // optimistic: the row is gone either way
    try {
      await decideKnock(slug, id, admit);
    } catch {
      refresh();
    }
  };

  if (waiting.length === 0) return null;

  return (
    <div className="absolute right-4 top-4 z-30 w-72 space-y-2">
      {waiting.map((p) => (
        <div key={p.id} className="l-sheet p-4">
          <p className="text-[10px] tracking-[0.24em] uppercase l-t-muted">Wants to join</p>
          <p className="mt-2 truncate text-[15px]" style={{ color: "var(--l-ink)" }}>
            {p.display_name || "Someone"}
          </p>
          <div className="mt-4 flex items-center gap-2">
            <button onClick={() => decide(p.id, true)} className="l-btn flex-1">
              Admit
            </button>
            <button onClick={() => decide(p.id, false)} className="l-btn-ghost">
              Deny
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
