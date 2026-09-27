export type SourceType =
  | "code"
  | "docs"
  | "ticket"
  | "slack"
  | "account"
  | "log"
  | "commit"
  | "pr"
  | "incident"
  | "screen"
  | "call";

export type Evidence = {
  id: string;
  source_type: SourceType;
  locator: string;
  snippet: string;
  score: number;
  retrieved_at: string;
};

export type ToolTraceEntry = {
  tool: string;
  args: Record<string, unknown>;
  ms: number;
  evidence: Evidence[];
};

export type Claim = { text: string; evidence_ids: string[] };

export type AgentAnswer = {
  answer: string;
  claims: Claim[];
  confidence: "high" | "medium" | "low";
  abstained: boolean;
  escalation: string | null;
  tool_trace: ToolTraceEntry[];
};

export const SOURCE_ICON: Record<SourceType, string> = {
  code: "💻",
  docs: "📄",
  ticket: "🎫",
  slack: "💬",
  account: "👤",
  log: "📋",
  commit: "🔀",
  pr: "🔃",
  incident: "🚨",
  screen: "🖥️",
  call: "📞",
};

/** A citation bracket, INCLUDING the multi-id form the compose model really
 *  emits: `[ev_a]` and `[ev_a, ev_b]`. The single-id version of this pattern
 *  matched nothing at all in `[ev_a, ev_b]`, so those brackets rendered
 *  literally on screen — the same bug the server-side verifier had (see
 *  server/app/agent/verifier.py's _MARKER_RE note), just never fixed here.
 *  Capture group 1 is the id list; split it on commas for the ids. */
export const CITATION_RE = /\[\s*(ev_[0-9a-f]+(?:\s*,\s*ev_[0-9a-f]+)*)\s*\]/g;

/** Citation ids for one matched bracket. */
export const citationIds = (group: string): string[] =>
  group.split(",").map((id) => id.trim()).filter(Boolean);

/** The answer with every citation marker removed, for surfaces that show
 *  plain prose rather than clickable chips (the whisper thread, the call
 *  chat, a Slack DM). The markers stay in the stored answer — they are the
 *  grounding contract, and the evidence panel renders them as chips — so
 *  this is purely the split between what is kept and what is shown, the same
 *  one call-agent/speech.py makes for what is SPOKEN.
 *
 *  Eats the space before the bracket too, so "platform [ev_x]." closes up to
 *  "platform." rather than leaving "platform ." */
export function stripCitations(text: string): string {
  return (text || "")
    .replace(/\s*\[\s*ev_[0-9a-f]+(?:\s*,\s*ev_[0-9a-f]+)*\s*\]/g, "")
    .replace(/\s+([,.;:!?])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

/** Flatten every tool call's evidence into one ev_id -> Evidence lookup.
 * Evidence only survives in tool_trace (the final `answer` only carries
 * [ev_xxx] markers + claims), so this is the only place the panel can get
 * locator/snippet/score/source_type back for a citation. */
export function buildEvidenceMap(toolTrace: ToolTraceEntry[]): Map<string, Evidence> {
  const map = new Map<string, Evidence>();
  for (const t of toolTrace) {
    for (const e of t.evidence || []) {
      if (!map.has(e.id)) map.set(e.id, e);
    }
  }
  return map;
}

/** The explain_why tool builds its evidence in exact hop order
 * (code -> commit -> pr -> slack...), so the provenance strip just needs
 * that one tool_trace entry's evidence, in order, unmodified. */
export function findProvenanceChain(toolTrace: ToolTraceEntry[]): Evidence[] | null {
  const hop = toolTrace.find((t) => t.tool === "explain_why" && t.evidence.length > 1);
  return hop ? hop.evidence : null;
}
