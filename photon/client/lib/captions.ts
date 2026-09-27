/** One transcription segment, attributed to whoever said it.
 *
 * `id` is LiveKit's own segment id: interim results arrive repeatedly under
 * the same id with growing text and `final: false`, then once more with
 * `final: true`. Callers should upsert by id, never append blindly, or a
 * single sentence renders as a dozen half-finished duplicates. */
export type Caption = {
  id: string;
  speaker: "user" | "agent";
  /** Display name — "You" for the local participant, "Photon" for the agent. */
  name: string;
  isLocal: boolean;
  text: string;
  final: boolean;
  at: number;
};

const MAX_CAPTIONS = 50;

/** Upsert a caption into the rolling transcript, keeping it bounded. */
export function mergeCaption(prev: Caption[], next: Caption): Caption[] {
  const i = prev.findIndex((c) => c.id === next.id);
  if (i === -1) return [...prev, next].slice(-MAX_CAPTIONS);
  const merged = [...prev];
  merged[i] = next;
  return merged;
}
