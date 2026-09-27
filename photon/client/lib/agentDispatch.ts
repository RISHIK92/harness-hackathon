/** Server-only: making sure a call actually gets its agent.
 *
 * The voice worker registers under a name (AGENT_DISPATCH_NAME, "photon"),
 * so LiveKit never sends it anywhere on its own — every room has to ask for
 * it. That is deliberate: automatic dispatch only fires when a room is
 * CREATED, so a worker that started late or restarted never reached a room
 * that already existed, and the call simply had no agent.
 *
 * `ensureAgent` is called on every join (the token route) and is idempotent:
 * a room that already has a live dispatch is left alone. `redispatch` is the
 * "bring it back" path — it drops dispatches whose job ended or never ran and
 * asks again, which is how a room recovers after the worker comes up.
 */
import { AgentDispatchClient } from "livekit-server-sdk";

export const AGENT_NAME = process.env.AGENT_DISPATCH_NAME || "photon";

// Mirrors @livekit/protocol's JobStatus; kept local so this module does not
// depend on the protocol package's enum export shape.
const JS_PENDING = 0;
const JS_RUNNING = 1;

type DispatchLike = {
  id: string;
  agentName: string;
  state?: { jobs?: { state?: { status?: number; error?: string } }[] };
};

function client(): AgentDispatchClient | null {
  const url = process.env.NEXT_PUBLIC_LIVEKIT_URL;
  const key = process.env.LIVEKIT_API_KEY;
  const secret = process.env.LIVEKIT_API_SECRET;
  if (!url || !key || !secret) return null;
  // The dispatch API is HTTP; the configured URL is the WebSocket one.
  return new AgentDispatchClient(url.replace(/^ws/, "http"), key, secret);
}

function jobsOf(d: DispatchLike) {
  return d.state?.jobs ?? [];
}

/** A dispatch still worth waiting on: no job yet (LiveKit is assigning it),
 *  or a job that is pending or running. */
function isLive(d: DispatchLike): boolean {
  const jobs = jobsOf(d);
  if (!jobs.length) return true;
  return jobs.some((j) => j.state?.status === JS_PENDING || j.state?.status === JS_RUNNING);
}

export type AgentDispatchStatus = {
  configured: boolean;
  dispatched: boolean;
  /** "running" once a worker has actually taken the job. */
  state: "none" | "waiting" | "running" | "ended" | "failed";
  error?: string;
};

export function summarise(dispatches: DispatchLike[]): AgentDispatchStatus {
  const ours = dispatches.filter((d) => d.agentName === AGENT_NAME);
  if (!ours.length) return { configured: true, dispatched: false, state: "none" };
  const jobs = ours.flatMap(jobsOf);
  if (jobs.some((j) => j.state?.status === JS_RUNNING)) return { configured: true, dispatched: true, state: "running" };
  const failed = jobs.find((j) => j.state?.error);
  if (failed) return { configured: true, dispatched: true, state: "failed", error: failed.state?.error };
  if (ours.some(isLive)) return { configured: true, dispatched: true, state: "waiting" };
  return { configured: true, dispatched: true, state: "ended" };
}

export async function agentStatus(room: string): Promise<AgentDispatchStatus> {
  const c = client();
  if (!c) return { configured: false, dispatched: false, state: "none", error: "LiveKit is not configured" };
  try {
    return summarise((await c.listDispatch(room)) as unknown as DispatchLike[]);
  } catch (e) {
    return { configured: true, dispatched: false, state: "none", error: String(e) };
  }
}

/** Idempotent: only asks when the room has no live dispatch for our agent.
 *  Never throws — a failed dispatch must not stop a person joining. */
export async function ensureAgent(room: string): Promise<AgentDispatchStatus> {
  const c = client();
  if (!c) return { configured: false, dispatched: false, state: "none", error: "LiveKit is not configured" };
  try {
    const existing = ((await c.listDispatch(room)) as unknown as DispatchLike[]).filter((d) => d.agentName === AGENT_NAME);
    if (existing.some(isLive)) return summarise(existing);
    await c.createDispatch(room, AGENT_NAME);
    return { configured: true, dispatched: true, state: "waiting" };
  } catch (e) {
    return { configured: true, dispatched: false, state: "none", error: String(e) };
  }
}

/** Drop dispatches that ended or never produced a running job, then ask
 *  again — the recovery path once a worker is (back) up. */
export async function redispatch(room: string): Promise<AgentDispatchStatus> {
  const c = client();
  if (!c) return { configured: false, dispatched: false, state: "none", error: "LiveKit is not configured" };
  try {
    const existing = ((await c.listDispatch(room)) as unknown as DispatchLike[]).filter((d) => d.agentName === AGENT_NAME);
    if (existing.some((d) => jobsOf(d).some((j) => j.state?.status === JS_RUNNING))) return summarise(existing);
    for (const d of existing) await c.deleteDispatch(d.id, room).catch(() => undefined);
    await c.createDispatch(room, AGENT_NAME);
    return { configured: true, dispatched: true, state: "waiting" };
  } catch (e) {
    return { configured: true, dispatched: false, state: "none", error: String(e) };
  }
}
