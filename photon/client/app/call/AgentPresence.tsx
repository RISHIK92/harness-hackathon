"use client";

/** Says so when the agent is not in the call — and why, and offers a fix.
 *
 * "The agent didn't join" used to have no visible cause at all: the room
 * opened and nobody came. The two usual reasons need different fixes, so
 * this tells them apart:
 *
 *   no voice worker running   → start it (scripts/dev.sh); nothing a button
 *                               in the browser can do
 *   worker up, never assigned → "Bring Photon in" re-dispatches the room
 *
 * Measured against a real LiveKit project: a room asked for the agent while
 * no worker was running is served the moment one starts (the request waits);
 * a room whose agent CRASHED is not — its job has ended, and a worker that
 * comes back leaves it alone. So when the worker is up and this room's
 * request has ended, the agent is asked for again automatically, once per
 * absence; the button stays as the manual path.
 *
 * Grace period first: a worker takes a few seconds to accept a job and
 * connect, and a warning that flashes on every healthy join trains people to
 * ignore it.
 */
import { useRemoteParticipants } from "@livekit/components-react";
import { ParticipantKind } from "livekit-client";
import { useCallback, useEffect, useState } from "react";
import { bringAgentBack, callAgentStatus, getToken, type CallAgentStatus } from "@/lib/api";

const GRACE_MS = 12_000;
const POLL_MS = 5_000;

export default function AgentPresence({ room, agentLabel = "Photon" }: { room: string; agentLabel?: string }) {
  const participants = useRemoteParticipants();
  const present = participants.some((p) => p.kind === ParticipantKind.AGENT || p.identity.startsWith("agent-"));
  const [overdue, setOverdue] = useState(false);
  const [status, setStatus] = useState<CallAgentStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoAsked, setAutoAsked] = useState(false);

  // (Re)start the grace period whenever the agent is missing — on join, and
  // again if it drops out mid-call.
  useEffect(() => {
    if (present) {
      const clear = setTimeout(() => { setOverdue(false); setAutoAsked(false); }, 0);
      return () => clearTimeout(clear);
    }
    const t = setTimeout(() => setOverdue(true), GRACE_MS);
    return () => clearTimeout(t);
  }, [present]);

  const refresh = useCallback(() => {
    callAgentStatus(room).then(setStatus).catch(() => setStatus(null));
  }, [room]);

  useEffect(() => {
    if (!overdue || present) return;
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [overdue, present, refresh]);

  // Recover a crashed agent without waiting for someone to notice the banner.
  useEffect(() => {
    if (present || autoAsked || !status || !getToken()) return;
    if (!status.worker.online || !["ended", "none"].includes(status.dispatch.state)) return;
    const t = setTimeout(() => {
      setAutoAsked(true);
      bringAgentBack(room).then(setStatus).catch(() => undefined);
    }, 0);
    return () => clearTimeout(t);
  }, [present, autoAsked, status, room]);

  if (present || !overdue) return null;

  const workerDown = status && !status.worker.online;
  const failed = status?.dispatch.state === "failed";
  const bring = async () => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await bringAgentBack(room));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-3 px-6 py-2 text-[12.5px]"
         style={{ background: "rgba(180,83,9,.16)", color: "var(--l-paper, #fffdf8)" }}>
      <span className="font-medium">{agentLabel} hasn&apos;t joined this call.</span>
      <span className="opacity-80">
        {!status
          ? "Checking why…"
          : workerDown
            ? "No voice worker is running — start it with scripts/dev.sh (call-agent), then bring it in."
            : failed
              ? `The worker took the call but failed: ${status.dispatch.error}`
              : status.dispatch.state === "waiting"
                ? "A worker is up and has been asked — it usually joins within seconds."
                : "The worker is up but this call was never assigned to it."}
      </span>
      {getToken() && (
        <button onClick={() => void bring()} disabled={busy}
                className="ml-auto rounded px-3 py-1 text-[12px] font-medium disabled:opacity-50"
                style={{ background: "var(--l-paper, #fffdf8)", color: "#1c1917" }}>
          {busy ? "Asking…" : `Bring ${agentLabel} in`}
        </button>
      )}
      {error && <span className="w-full opacity-90">{error}</span>}
    </div>
  );
}
