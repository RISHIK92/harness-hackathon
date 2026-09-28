import { AccessToken, RoomAgentDispatch, RoomConfiguration } from "livekit-server-sdk";
import { AGENT_NAME, ensureAgent } from "@/lib/agentDispatch";
import { NextRequest, NextResponse } from "next/server";

const BRAIN_API = process.env.NEXT_PUBLIC_BRAIN_API_URL || "http://localhost:8000";

/** Mints a LiveKit join token. Server-side only — holds LIVEKIT_API_SECRET,
 * which the browser never sees.
 *
 * The identity is derived from the caller's own session, NEVER from a query
 * parameter. It used to be `?identity=whatever-you-type`, which meant
 * anyone could join as anyone — and now that the agent attributes each
 * spoken turn to a participant identity (and may use that person's private
 * sources to answer), a forgeable identity would be a way to read someone
 * else's data by simply typing their name.
 *
 * Guests are still allowed: no token means a guest identity, which the
 * agent treats as viewer-level with workspace-scoped sources only.
 */
export async function GET(req: NextRequest) {
  const room = req.nextUrl.searchParams.get("room") || "photon";
  const guestName = (req.nextUrl.searchParams.get("name") || "").trim().slice(0, 40);
  // Proof that someone inside the call let this person in. The waiting room
  // is only real if a token cannot be obtained without passing through it,
  // so this is verified against the API rather than trusted.
  const knockId = req.nextUrl.searchParams.get("knock");
  const authHeader = req.headers.get("authorization");

  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;
  const url = process.env.NEXT_PUBLIC_LIVEKIT_URL;
  if (!apiKey || !apiSecret || !url) {
    return NextResponse.json({ error: "LiveKit is not configured on the server" }, { status: 500 });
  }

  let identity: string;
  let name: string;
  let metadata: Record<string, unknown>;

  if (authHeader) {
    // Verified against the API rather than decoded here: the signing key
    // lives in the backend, and a token this route merely *parsed* would be
    // trivially forgeable.
    const me = await fetch(`${BRAIN_API}/api/auth/me`, { headers: { authorization: authHeader, "ngrok-skip-browser-warning": "1" } });
    if (!me.ok) {
      return NextResponse.json({ error: "Your session expired — sign in again" }, { status: 401 });
    }
    const user = await me.json();

    // Being signed in is not the same as being let into THIS call — only a
    // workspace member of the meeting's own workspace gets in for free.
    // Everyone else (a registered user who just isn't on this team) is a
    // stranger to this room and must pass the same admission check a guest
    // does, or the waiting room is trivially bypassable by anyone with an
    // account.
    const meetingRes = await fetch(`${BRAIN_API}/api/meetings/${encodeURIComponent(room)}`, {
      headers: { authorization: authHeader, "ngrok-skip-browser-warning": "1" },
    });
    if (!meetingRes.ok) {
      return NextResponse.json({ error: "No meeting with that code" }, { status: 404 });
    }
    const meeting = await meetingRes.json();

    const workspacesRes = await fetch(`${BRAIN_API}/api/workspaces`, {
      headers: { authorization: authHeader, "ngrok-skip-browser-warning": "1" },
    });
    const workspaces = workspacesRes.ok ? await workspacesRes.json() : [];
    const isMember = workspaces.some((w: { id: string }) => w.id === meeting.workspace_id);

    if (!isMember) {
      if (!knockId) {
        return NextResponse.json(
          { error: "Ask to join first — someone in the call has to let you in" },
          { status: 403 }
        );
      }
      const admission = await fetch(
        `${BRAIN_API}/api/meetings/${encodeURIComponent(room)}/admission/${encodeURIComponent(knockId)}`
      );
      const verdict = admission.ok ? await admission.json() : { admitted: false };
      if (!verdict.admitted) {
        return NextResponse.json(
          { error: "You haven't been admitted to this call yet" },
          { status: 403 }
        );
      }
    }

    identity = `user:${user.id}`;
    name = user.email;
    // `member` decides who receives the agent's trace events, which carry
    // internal evidence (call-agent/adapters/livekit_adapter.py). Signed
    // into this token, so it cannot be claimed from the browser.
    metadata = { user_id: user.id, email: user.email, guest: false, member: isMember };
  } else {
    if (!guestName) {
      return NextResponse.json({ error: "A name is required to join as a guest" }, { status: 400 });
    }
    if (!knockId) {
      return NextResponse.json(
        { error: "Ask to join first — someone in the call has to let you in" },
        { status: 403 }
      );
    }
    const admission = await fetch(
      `${BRAIN_API}/api/meetings/${encodeURIComponent(room)}/admission/${encodeURIComponent(knockId)}`
    );
    const verdict = admission.ok ? await admission.json() : { admitted: false };
    if (!verdict.admitted || !verdict.identity_key) {
      return NextResponse.json(
        { error: "You haven't been admitted to this call yet" },
        { status: 403 }
      );
    }
    // Bound to the admission, not to what this request says: the name the
    // admitting member saw, and one identity per admitted knock. A random
    // identity and the ?name= parameter used to let one admitted knock mint
    // any number of guests under any names; now a second join with the same
    // knock is the same participant (LiveKit replaces the older session).
    identity = `guest:${verdict.identity_key}`;
    name = (verdict.display_name || guestName).slice(0, 40);
    metadata = { guest: true, member: false, display_name: name };
  }

  const at = new AccessToken(apiKey, apiSecret, {
    identity,
    name,
    ttl: "1h",
    // Read by the agent to resolve a speaker to a user without trusting
    // anything the client says at runtime — LiveKit signs this.
    metadata: JSON.stringify(metadata),
  });
  at.addGrant({ room, roomJoin: true, canPublish: true, canSubscribe: true, canPublishData: true });

  // Every join makes sure the call has its agent (lib/agentDispatch.ts):
  // idempotent, so the second person to join does not summon a second one.
  // If asking fails outright (e.g. the room does not exist yet), the token
  // carries the request instead, and LiveKit dispatches when this join
  // creates the room — one or the other, never both.
  const agent = await ensureAgent(room);
  if (!agent.dispatched && agent.configured) {
    at.roomConfig = new RoomConfiguration({ agents: [new RoomAgentDispatch({ agentName: AGENT_NAME })] });
  }

  return NextResponse.json({ token: await at.toJwt(), url, room, identity, name, agent });
}
