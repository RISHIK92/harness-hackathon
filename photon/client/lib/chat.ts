/** Human-to-human chat, carried on the LiveKit data channel.
 *
 * Deliberately our own rather than the components library's prefab chat: the
 * prefab renders in LiveKit's dark theme, and on this paper surface it would
 * read as a foreign window bolted to the side of the call.
 *
 * The sender's identity is NOT taken from the payload — LiveKit authenticates
 * the packet and tells us who sent it, so anything in the body would be
 * self-asserted. Only the text travels.
 */
export const CHAT_TOPIC = "photon.chat";

export type ChatMessage = {
  id: string;
  /** LiveKit participant identity, from the packet — never from the body. */
  from: string;
  name: string;
  text: string;
  at: number;
  local: boolean;
};

const MAX_MESSAGES = 200;

export function appendChat(prev: ChatMessage[], next: ChatMessage): ChatMessage[] {
  if (prev.some((m) => m.id === next.id)) return prev; // a redelivered packet
  return [...prev, next].slice(-MAX_MESSAGES);
}

// Typed as Uint8Array<ArrayBuffer> rather than the default ArrayBufferLike:
// livekit-client's publishData refuses a possibly-SharedArrayBuffer view.
export function encodeChat(text: string, id: string): Uint8Array<ArrayBuffer> {
  return new TextEncoder().encode(JSON.stringify({ id, text })) as Uint8Array<ArrayBuffer>;
}

export function decodeChat(payload: Uint8Array): { id: string; text: string } | null {
  try {
    const body = JSON.parse(new TextDecoder().decode(payload));
    if (typeof body?.text !== "string" || typeof body?.id !== "string") return null;
    return { id: body.id, text: body.text.slice(0, 2000) };
  } catch {
    return null; // not ours, or malformed — never let it break the call
  }
}
