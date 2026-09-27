import { NextRequest, NextResponse } from "next/server";
import { agentStatus, redispatch } from "@/lib/agentDispatch";

const BRAIN_API = process.env.NEXT_PUBLIC_BRAIN_API_URL || "http://localhost:8000";

/** Is the agent coming, and if not, why — and a way to ask again.
 *
 *   GET  ?room=<slug>   dispatch state for the room + whether a worker is alive
 *   POST ?room=<slug>   re-dispatch (signed-in members only)
 *
 * The two facts together are what the call screen needs to say something
 * true: "no voice worker is running" and "the worker is up but was never
 * asked" are different problems with different fixes.
 */
async function workerStatus() {
  try {
    const r = await fetch(`${BRAIN_API}/api/agent-worker/status`, { cache: "no-store" });
    return r.ok ? await r.json() : { online: false, detail: "Can't reach Photon's API" };
  } catch {
    return { online: false, detail: "Can't reach Photon's API" };
  }
}

export async function GET(req: NextRequest) {
  const room = req.nextUrl.searchParams.get("room");
  if (!room) return NextResponse.json({ error: "room is required" }, { status: 400 });
  const [dispatch, worker] = await Promise.all([agentStatus(room), workerStatus()]);
  return NextResponse.json({ dispatch, worker });
}

export async function POST(req: NextRequest) {
  const room = req.nextUrl.searchParams.get("room");
  const auth = req.headers.get("authorization");
  if (!room) return NextResponse.json({ error: "room is required" }, { status: 400 });
  if (!auth) return NextResponse.json({ error: "Sign in to bring the agent back" }, { status: 401 });
  // Only someone who can see the meeting may summon an agent into it.
  const meeting = await fetch(`${BRAIN_API}/api/meetings/${encodeURIComponent(room)}`, {
    headers: { authorization: auth, "ngrok-skip-browser-warning": "1" },
  });
  if (!meeting.ok) return NextResponse.json({ error: "No meeting with that code" }, { status: 404 });
  const [dispatch, worker] = await Promise.all([redispatch(room), workerStatus()]);
  return NextResponse.json({ dispatch, worker });
}
