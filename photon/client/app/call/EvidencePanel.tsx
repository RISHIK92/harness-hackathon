"use client";

import { useMemo, useRef, useState } from "react";
import CodeSnippet from "./CodeSnippet";
import {
  AgentAnswer,
  CITATION_RE,
  citationIds,
  Evidence,
  SOURCE_ICON,
  buildEvidenceMap,
  findProvenanceChain,
} from "@/lib/evidence";

type Turn = { role: "user" | "agent"; question?: string; result?: AgentAnswer };

const CONFIDENCE_COLOR: Record<string, string> = {
  high: "text-[color:var(--l-rust)]",
  medium: "text-[color:var(--l-terra)]",
  low: "text-[color:var(--l-rust)]",
};

function AnswerText({
  text,
  evidenceMap,
  onCite,
}: {
  text: string;
  evidenceMap: Map<string, Evidence>;
  onCite: (id: string) => void;
}) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  const re = new RegExp(CITATION_RE);
  let key = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(<span key={key++}>{text.slice(last, m.index)}</span>);
    // One chip per id: the model really does emit [ev_a, ev_b], and a single
    // chip for two sources would hide one of them.
    for (const id of citationIds(m[1])) {
    const known = evidenceMap.has(id);
    parts.push(
      <button
        key={key++}
        onClick={() => known && onCite(id)}
        className={`inline-flex items-center rounded px-1.5 py-0.5 mx-0.5 text-xs font-mono align-middle ${
          known
            ? "bg-[rgba(180,83,9,.09)] text-[color:var(--l-rust)] hover:bg-[rgba(180,83,9,.16)] cursor-pointer"
            : "bg-[rgba(28,25,23,.05)] text-[color:var(--l-muted)] line-through cursor-not-allowed"
        }`}
        title={known ? evidenceMap.get(id)!.locator : "unresolved citation"}
      >
        {known ? (evidenceMap.get(id)!.source_type === "code" ? "💻" : SOURCE_ICON[evidenceMap.get(id)!.source_type]) : "⚠️"}
      </button>
    );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(<span key={key++}>{text.slice(last)}</span>);
  return <p className="leading-relaxed text-sm text-[color:var(--l-ink)]">{parts}</p>;
}

function EvidenceCard({
  ev,
  highlighted,
  cardRef,
}: {
  ev: Evidence;
  highlighted: boolean;
  cardRef: (el: HTMLDivElement | null) => void;
}) {
  return (
    <div
      ref={cardRef}
      className={`border rounded-lg p-3 text-sm transition-colors ${
        highlighted ? "border-[color:var(--l-rust)] bg-[rgba(180,83,9,.06)]" : "border-[color:var(--l-rule)] bg-[rgba(28,25,23,.03)]"
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-mono text-[color:var(--l-muted)]">
          {SOURCE_ICON[ev.source_type]} {ev.source_type}
        </span>
        <span className="text-xs text-[color:var(--l-muted)]">score {ev.score.toFixed(2)}</span>
      </div>
      {ev.source_type === "code" ? (
        // Code gets real line numbers and highlighting; prose does not need
        // either, and a monospace block would make a Slack message harder
        // to read rather than easier.
        <CodeSnippet locator={ev.locator} code={ev.snippet} />
      ) : (
        <>
          <div className="font-mono text-xs text-[color:var(--l-ink-2)] mb-1 break-all">{ev.locator}</div>
          <pre className="text-xs text-[color:var(--l-ink-2)] whitespace-pre-wrap break-words max-h-32 overflow-y-auto">
            {ev.snippet}
          </pre>
        </>
      )}
    </div>
  );
}

function ProvenanceStrip({ chain }: { chain: Evidence[] }) {
  return (
    <div className="mb-4">
      <h3 className="text-xs uppercase tracking-wide text-[color:var(--l-muted)] mb-2">
        Provenance — why this exists
      </h3>
      <div className="flex items-center gap-2 overflow-x-auto pb-2">
        {chain.map((ev, i) => (
          <div key={ev.id} className="flex items-center gap-2 shrink-0">
            <div className="border border-[color:var(--l-rule)] bg-[rgba(28,25,23,.03)] rounded-lg px-3 py-2 max-w-[220px]">
              <div className="text-xs font-mono text-[color:var(--l-muted)] mb-0.5">
                {SOURCE_ICON[ev.source_type]} {ev.source_type}
              </div>
              <div className="text-xs text-[color:var(--l-ink-2)] truncate">{ev.locator}</div>
            </div>
            {i < chain.length - 1 && <span className="text-[color:var(--l-muted)]">→</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function EvidencePanel({ turns }: { turns: Turn[] }) {
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const latest = [...turns].reverse().find((t) => t.role === "agent" && t.result);
  const evidenceMap = useMemo(
    () => (latest?.result ? buildEvidenceMap(latest.result.tool_trace) : new Map()),
    [latest]
  );
  const provenanceChain = useMemo(
    () => (latest?.result ? findProvenanceChain(latest.result.tool_trace) : null),
    [latest]
  );

  const scrollToEvidence = (id: string) => {
    setHighlightedId(id);
    cardRefs.current.get(id)?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => setHighlightedId((cur) => (cur === id ? null : cur)), 2000);
  };

  return (
    <div className="flex flex-col gap-4 h-full">
      <div className="flex-1 overflow-y-auto space-y-4 min-h-0">
        {turns.map((t, i) =>
          t.role === "user" ? (
            <div key={i} className="text-[color:var(--l-ink)] text-sm">
              <span className="text-xs uppercase tracking-wide text-[color:var(--l-muted)] mr-2">you</span>
              {t.question}
            </div>
          ) : (
            <div key={i} className="border border-[color:var(--l-rule)] rounded-lg p-3 bg-[rgba(28,25,23,.03)]/50">
              {t.result ? (
                <>
                  <AnswerText
                    text={t.result.answer}
                    evidenceMap={buildEvidenceMap(t.result.tool_trace)}
                    onCite={scrollToEvidence}
                  />
                  <div className="flex items-center gap-3 mt-2 text-xs">
                    <span className={CONFIDENCE_COLOR[t.result.confidence]}>
                      confidence: {t.result.confidence}
                    </span>
                    {t.result.abstained && <span className="text-[color:var(--l-terra)]">abstained</span>}
                    {t.result.escalation && (
                      <span className="text-[color:var(--l-ink-2)]">→ {t.result.escalation}</span>
                    )}
                  </div>
                  {t.result.tool_trace.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {t.result.tool_trace.map((tr, j) => (
                        <span
                          key={j}
                          className="text-[10px] font-mono bg-[rgba(28,25,23,.06)] text-[color:var(--l-ink-2)] rounded px-1.5 py-0.5"
                        >
                          {tr.tool} · {tr.ms}ms
                        </span>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <span className="text-[color:var(--l-ink-2)] text-sm animate-pulse">thinking…</span>
              )}
            </div>
          )
        )}
      </div>

      {latest?.result && evidenceMap.size > 0 && (
        <div className="border-t border-[color:var(--l-rule)] pt-4 max-h-[45%] overflow-y-auto shrink-0">
          {provenanceChain && <ProvenanceStrip chain={provenanceChain} />}
          <h3 className="text-xs uppercase tracking-wide text-[color:var(--l-muted)] mb-2">
            Evidence ({evidenceMap.size})
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {[...evidenceMap.values()].map((ev) => (
              <EvidenceCard
                key={ev.id}
                ev={ev}
                highlighted={highlightedId === ev.id}
                cardRef={(el) => {
                  if (el) cardRefs.current.set(ev.id, el);
                }}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}