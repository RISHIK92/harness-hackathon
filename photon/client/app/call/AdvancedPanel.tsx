"use client";

import { useEffect, useState } from "react";
import { fmtMs, turnTotals, type Stage, type ToolCall, type TurnTrace } from "@/lib/trace";

/** The "advanced" view that sits beside the transcript: what the agent is
 * doing internally, right now — stage by stage, tool by tool, with real
 * latencies. Everything here comes from live events (lib/trace.ts); nothing
 * is estimated or interpolated on the client. */
export default function AdvancedPanel({ turns }: { turns: TurnTrace[] }) {
  const current = turns[turns.length - 1];
  const older = turns.slice(0, -1).reverse();

  return (
    <div className="flex flex-col min-h-0">
      <div className="flex items-center justify-between mb-1 shrink-0">
        <h2 className="text-xs uppercase tracking-wide text-[color:var(--l-muted)]">Advanced · agent pipeline</h2>
        {current && <LiveClock turn={current} />}
      </div>

      <div className="bg-[rgba(28,25,23,.03)] border border-[color:var(--l-rule)] rounded p-3 h-36 overflow-y-auto text-xs">
        {!current ? (
          <p className="text-[color:var(--l-muted)]">
            Idle. Ask a question by voice or text and every plan, tool call and LLM hop shows up here as it
            happens.
          </p>
        ) : (
          <TurnView turn={current} />
        )}
        {older.length > 0 && (
          <div className="mt-3 pt-2 border-t border-[color:var(--l-rule)] space-y-1">
            {older.map((t) => (
              <PreviousTurn key={t.id} turn={t} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** Ticks while a turn is in flight so latency is visible as it accrues,
 * not only once the turn is over. Freezes on the server-reported total the
 * moment the turn ends — the server's number is the authoritative one
 * (it excludes network and render time). */
function LiveClock({ turn }: { turn: TurnTrace }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (turn.status !== "running") return;
    const handle = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(handle);
  }, [turn.status, turn.id]);

  const elapsed = turn.totalMs ?? now - turn.startedAt;
  const running = turn.status === "running";

  return (
    <div className="flex items-center gap-2">
      <span
        className={`text-[10px] px-1.5 py-0.5 rounded border ${
          turn.source === "voice"
            ? "border-[rgba(180,83,9,.4)] text-[color:var(--l-rust)]"
            : "border-[color:var(--l-rule)] text-[#0369a1]"
        }`}
      >
        {turn.source}
      </span>
      <span
        className={`font-mono text-[11px] tabular-nums ${running ? "text-[color:var(--l-terra)]" : "text-[color:var(--l-ink-2)]"}`}
      >
        {running && <span className="inline-block w-1.5 h-1.5 rounded-full bg-[color:var(--l-terra)] animate-pulse mr-1" />}
        {fmtMs(elapsed)}
      </span>
    </div>
  );
}

function TurnView({ turn }: { turn: TurnTrace }) {
  const totals = turnTotals(turn);
  // Bars are scaled against the slowest step in THIS turn, so one 9s
  // compose doesn't flatten every tool call into an invisible sliver.
  const slowest = Math.max(
    1,
    ...turn.stages.map((s) => s.ms ?? 0),
    ...turn.tools.map((c) => c.ms ?? 0)
  );

  const rows: { stage: Stage; tools: ToolCall[] }[] = turn.stages.map((stage) => ({
    stage,
    // Tool calls hang off the plan round that chose them.
    tools: stage.key.startsWith("plan-")
      ? turn.tools.filter((c) => `plan-${c.round}` === stage.key)
      : [],
  }));

  return (
    <div className="space-y-1.5">
      <p className="text-[color:var(--l-ink-2)] truncate" title={turn.question}>
        {turn.question ? `“${turn.question}”` : "…"}
      </p>

      {rows.map(({ stage, tools }) => (
        <div key={stage.key}>
          <Row
            label={stage.label}
            detail={stage.detail}
            ms={stage.ms}
            state={stage.state}
            slowest={slowest}
            marker={stage.marker}
          />
          {tools.map((call) => (
            <div key={call.id} className="ml-3 border-l border-[color:var(--l-rule)] pl-2">
              <Row
                label={call.tool}
                mono
                detail={[
                  argsSummary(call.args),
                  call.evidenceCount !== undefined ? `${call.evidenceCount} evidence` : undefined,
                  call.note || undefined,
                ]
                  .filter(Boolean)
                  .join(" · ")}
                ms={call.ms}
                state={
                  call.ms === undefined
                    ? "running"
                    : call.status === "error" || call.status === "blocked"
                      ? "failed"
                      : "done"
                }
                slowest={slowest}
              />
            </div>
          ))}
        </div>
      ))}

      {turn.error && <p className="text-[color:var(--l-rust)]">error: {turn.error}</p>}

      {turn.status !== "running" && (
        <p className="text-[10px] text-[color:var(--l-muted)] pt-1 border-t border-[color:var(--l-rule)]">
          total {fmtMs(turn.totalMs ?? 0)} · {totals.toolCount} tool call
          {totals.toolCount === 1 ? "" : "s"} {fmtMs(totals.toolMs)} · llm {fmtMs(totals.llmMs)}
          {turn.confidence && ` · confidence ${turn.confidence}`}
          {turn.abstained && " · abstained"}
        </p>
      )}
    </div>
  );
}

function Row({
  label,
  detail,
  ms,
  state,
  slowest,
  mono,
  marker,
}: {
  label: string;
  detail?: string;
  ms?: number;
  state: "running" | "done" | "failed";
  slowest: number;
  mono?: boolean;
  marker?: boolean;
}) {
  const color =
    state === "running" ? "bg-[color:var(--l-terra)]" : state === "failed" ? "bg-[color:var(--l-rust)]" : "bg-[color:var(--l-rust)]";
  const width = marker || ms === undefined ? 0 : Math.max(2, ((ms ?? 0) / slowest) * 100);

  return (
    <div className="flex items-baseline gap-2 py-0.5">
      <span
        className={`shrink-0 w-1.5 h-1.5 rounded-full ${color} ${state === "running" ? "animate-pulse" : ""}`}
      />
      <span className={`shrink-0 ${mono ? "font-mono text-[color:var(--l-rust)]" : "text-[color:var(--l-ink)]"}`}>{label}</span>
      {detail && <span className="text-[color:var(--l-muted)] truncate">{detail}</span>}
      <span className="flex-1 min-w-[12px] h-1 relative">
        {width > 0 && (
          <span
            className={`absolute inset-y-0 left-0 rounded-sm ${color} opacity-40`}
            style={{ width: `${width}%` }}
          />
        )}
      </span>
      <span className="shrink-0 font-mono tabular-nums text-[color:var(--l-ink-2)] text-[11px]">
        {state === "running" ? "running…" : ms !== undefined && !marker ? fmtMs(ms) : ""}
      </span>
    </div>
  );
}

function PreviousTurn({ turn }: { turn: TurnTrace }) {
  const totals = turnTotals(turn);
  return (
    <p className="text-[10px] text-[color:var(--l-muted)] truncate">
      {fmtMs(turn.totalMs ?? 0)} · {totals.toolCount} tool{totals.toolCount === 1 ? "" : "s"} ·{" "}
      {turn.abstained ? "abstained" : turn.confidence ?? turn.status} — {turn.question}
    </p>
  );
}

function argsSummary(args: Record<string, unknown>): string | undefined {
  const parts = Object.entries(args)
    // repo_id is forced by the loop on every repo-scoped tool and is a
    // long UUID — it's noise in a one-line summary.
    .filter(([k]) => k !== "repo_id")
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`);
  if (parts.length === 0) return undefined;
  const joined = parts.join(", ");
  return joined.length > 60 ? `${joined.slice(0, 57)}…` : joined;
}
