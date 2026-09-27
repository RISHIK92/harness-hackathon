/** Client-side model of one agent turn's live pipeline.
 *
 * The brain-api emits flat events (app/agent/events.py) as the turn runs;
 * this folds them into something renderable. Two sources feed the exact
 * same reducer:
 *   - voice — events arrive over the LiveKit data channel, republished by
 *     call-agent's orchestrator (topic "photon.trace")
 *   - text  — events arrive over SSE from POST /api/agent/ask/stream
 * so the advanced panel doesn't care which path a turn came from.
 */
import type { AgentAnswer } from "./evidence";

export type TraceEvent = {
  type: string;
  t?: number;
  seq?: number;
  turn_id?: string;
  source?: "voice" | "text";
  [key: string]: unknown;
};

export type StageState = "running" | "done" | "failed";

export type Stage = {
  key: string;
  label: string;
  /** ms since turn start when this stage began — its position on the timeline. */
  startedT: number;
  /** duration; undefined while still running. */
  ms?: number;
  detail?: string;
  state: StageState;
  /** true for zero-duration markers (evidence dedup) — rendered without a bar. */
  marker?: boolean;
};

export type ToolCall = {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  round: number;
  startedT: number;
  ms?: number;
  status?: "ok" | "empty" | "error" | string;
  evidenceCount?: number;
  note?: string | null;
};

export type TurnTrace = {
  id: string;
  question: string;
  source: "voice" | "text";
  /** wall-clock ms, used only to tick the live elapsed counter. */
  startedAt: number;
  stages: Stage[];
  tools: ToolCall[];
  totalMs?: number;
  status: "running" | "done" | "error";
  error?: string;
  confidence?: string;
  abstained?: boolean;
  evidenceCount?: number;
  result?: AgentAnswer;
  seenSeq: number[];
};

const MAX_TURNS = 8;

function newTurn(id: string, question: string, source: "voice" | "text"): TurnTrace {
  return {
    id,
    question,
    source,
    startedAt: Date.now(),
    stages: [],
    tools: [],
    status: "running",
    seenSeq: [],
  };
}

function upsertStage(turn: TurnTrace, key: string, patch: Partial<Stage> & { label?: string }): void {
  const existing = turn.stages.find((s) => s.key === key);
  if (existing) Object.assign(existing, patch);
  else
    turn.stages.push({
      key,
      label: patch.label ?? key,
      startedT: patch.startedT ?? 0,
      state: patch.state ?? "running",
      ...patch,
    });
}

const num = (v: unknown, fallback = 0): number => (typeof v === "number" ? v : fallback);
const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);

/** Fold one event into the turn list (newest last). Pure: returns new state. */
export function applyTraceEvent(
  turns: TurnTrace[],
  event: TraceEvent,
  fallbackTurnId: string
): TurnTrace[] {
  const id = event.turn_id || fallbackTurnId;
  const source = event.source === "voice" ? "voice" : "text";
  const next = turns.map((t) => (t.id === id ? { ...t, stages: [...t.stages], tools: [...t.tools] } : t));

  let turn = next.find((t) => t.id === id);
  if (!turn) {
    turn = newTurn(id, str(event.question) ?? "", source);
    next.push(turn);
  }

  // The data channel is reliable, but a re-delivered packet would otherwise
  // duplicate a tool row or double-count a stage.
  const seq = num(event.seq, 0);
  if (seq > 0) {
    if (turn.seenSeq.includes(seq)) return turns;
    turn.seenSeq = [...turn.seenSeq, seq];
  }

  const t = num(event.t, 0);

  switch (event.type) {
    case "turn.requested":
    case "turn.start":
      if (str(event.question)) turn.question = str(event.question)!;
      break;

    case "turn.fastpath":
      // Answered locally with no LLM and no tools (a greeting, or ambient
      // speech the agent deliberately stayed silent on). Shown so the
      // panel reads as a deliberate 0ms path, not a missed turn.
      upsertStage(turn, "fastpath", {
        label: event.intent === "greeting" ? "Greeting — answered locally" : "Ambient speech — ignored",
        startedT: t,
        ms: 0,
        marker: true,
        state: "done",
        detail: "no plan, no tools, no LLM",
      });
      break;

    // Conversation memory was applied. Shown because a resolved follow-up
    // is otherwise invisible: the panel would report a search for "why is
    // that" while the tool actually ran on something else entirely, and a
    // resolution that went wrong would look like plain retrieval failure.
    case "history.used":
      upsertStage(turn, "history", {
        label: event.resolved ? "Follow-up resolved from memory" : "Conversation memory",
        startedT: t,
        ms: 0,
        marker: true,
        state: "done",
        detail: event.resolved
          ? `searched as "${str(event.query)}"`
          : `${num(event.turns)} earlier turn(s) in context`,
      });
      break;

    case "vision.start":
      upsertStage(turn, "vision", { label: "Screen frame analysis", startedT: t, state: "running" });
      break;
    case "vision.done":
      upsertStage(turn, "vision", {
        ms: num(event.ms),
        state: event.ok ? "done" : "failed",
        detail: event.ok ? "frame described" : "vision call failed — frame dropped",
      });
      break;

    case "plan.start":
      upsertStage(turn, `plan-${num(event.round)}`, {
        label: `Plan · round ${num(event.round)}`,
        startedT: t,
        state: "running",
      });
      break;
    case "plan.retry":
      upsertStage(turn, `plan-${num(event.round)}`, { detail: "empty plan — retrying" });
      break;
    case "plan.done": {
      const calls = (event.calls as { tool: string }[] | undefined) ?? [];
      upsertStage(turn, `plan-${num(event.round)}`, {
        ms: num(event.ms),
        state: "done",
        detail: calls.length ? `chose ${calls.map((c) => c.tool).join(", ")}` : "no further tools needed",
      });
      break;
    }

    case "tool.start":
      turn.tools.push({
        id: str(event.id) ?? `${seq}`,
        tool: str(event.tool) ?? "unknown",
        args: (event.args as Record<string, unknown>) ?? {},
        round: num(event.round, 1),
        startedT: t,
      });
      break;

    // The tool never ran — a meeting's allowed_tools list excluded it — so
    // there's no matching tool.start/tool.done pair, just this one event.
    // Shown as a distinct "blocked" row rather than silently missing, so a
    // restricted call reads as a deliberate block, not a broken panel.
    case "tool.blocked":
      turn.tools.push({
        id: str(event.id) ?? `${seq}`,
        tool: str(event.tool) ?? "unknown",
        args: {},
        round: num(event.round, 1),
        startedT: t,
        ms: 0,
        status: "blocked",
        note: `${str(event.tool) ?? "this tool"} is not enabled for this call`,
      });
      break;
    // Round 2 was never run — round 1 already found evidence, so the
    // second LLM round-trip was skipped to save latency (see loop.py). Shown
    // as a zero-duration marker stage so the panel proves it wasn't lost.
    case "plan.skipped":
      upsertStage(turn, `plan-${num(event.round)}`, {
        label: `Plan · round ${num(event.round)}`,
        startedT: t,
        ms: 0,
        marker: true,
        state: "done",
        detail: str(event.reason) ?? "skipped",
      });
      break;

    case "tool.done": {
      const call = turn.tools.find((c) => c.id === event.id);
      if (call) {
        call.ms = num(event.ms);
        call.status = str(event.status);
        call.evidenceCount = num(event.evidence_count);
        call.note = str(event.note) ?? null;
      }
      break;
    }

    case "evidence.gathered":
      turn.evidenceCount = num(event.count);
      upsertStage(turn, "evidence", {
        label: "Evidence deduped",
        startedT: t,
        ms: 0,
        marker: true,
        state: "done",
        detail: `${num(event.count)} unique of ${num(event.raw_count)}`,
      });
      break;

    case "compose.start":
      upsertStage(turn, "compose", { label: "Compose answer", startedT: t, state: "running" });
      break;
    case "compose.done":
      upsertStage(turn, "compose", {
        ms: num(event.ms),
        state: event.parsed === false ? "failed" : "done",
        detail: event.parsed === false ? "model returned unparseable JSON" : undefined,
      });
      break;

    case "verify.done": {
      const kept = num(event.claims_kept);
      const stripped = num(event.claims_in) - kept;
      upsertStage(turn, "verify", {
        label: "Verify citations",
        startedT: t,
        ms: num(event.ms),
        state: stripped > 0 ? "failed" : "done",
        detail: stripped > 0 ? `${stripped} uncited claim(s) stripped` : `${kept} claim(s) kept`,
      });
      turn.confidence = str(event.confidence);
      turn.abstained = event.abstained === true;
      break;
    }

    case "turn.done":
      turn.totalMs = num(event.ms);
      turn.status = "done";
      turn.result = event.result as AgentAnswer | undefined;
      if (turn.result) {
        turn.confidence = turn.result.confidence;
        turn.abstained = turn.result.abstained;
      }
      // Anything still marked running when the turn ends never reported
      // back — show it as failed rather than spinning forever.
      turn.stages.forEach((s) => {
        if (s.state === "running") s.state = "failed";
      });
      break;

    case "turn.error":
      turn.status = "error";
      turn.error = str(event.error) ?? "unknown error";
      turn.stages.forEach((s) => {
        if (s.state === "running") s.state = "failed";
      });
      break;
  }

  return next.slice(-MAX_TURNS);
}

/** Where the wall-clock time actually went, for the totals footer. */
export function turnTotals(turn: TurnTrace) {
  const toolMs = turn.tools.reduce((sum, c) => sum + (c.ms ?? 0), 0);
  const llmMs = turn.stages
    .filter((s) => s.key.startsWith("plan") || s.key === "compose" || s.key === "vision")
    .reduce((sum, s) => sum + (s.ms ?? 0), 0);
  return { toolMs, llmMs, toolCount: turn.tools.length };
}

export const fmtMs = (ms: number): string => (ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`);
