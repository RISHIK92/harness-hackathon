"use client";

import { useEffect, useState } from "react";
import { readFile } from "@/lib/api";
import { parseLocator } from "@/lib/highlight";
import { CITATION_RE, citationIds, type AgentAnswer, type Evidence } from "@/lib/evidence";
import CodeSnippet from "./CodeSnippet";

type Cited = {
  locator: string;
  /** The chunk the agent actually reasoned over — the fallback if the real
   *  file can't be read (repo deleted, path renamed, brain-api down). */
  snippet: string;
  /** Whichever tool call produced this evidence forced its own repo_id, so
   *  the file can be read back from the right repo even when a workspace
   *  holds many. */
  repoId: string | null;
};

/** Every file the agent has cited this call, rendered as real code.
 *
 * Two things this deliberately does NOT do:
 *
 * - It does not render `evidence.snippet`. That snippet is the embedded
 *   chunk, and a chunk does not reliably cover the line range its locator
 *   claims: `authController.ts:L1-L39` comes back as five lines of
 *   imports, and some chunks drop lines out of the middle. CodeSnippet
 *   numbers lines from the locator's start, so showing the chunk means
 *   printing confident, wrong line numbers — which breaks the one promise
 *   this panel makes, that someone can say "line 47" and everyone lands in
 *   the same place. So each locator is read back off disk via read_file.
 * - It does not show every chunk RETRIEVED, only the ones the answer
 *   actually cited. On a code-only workspace the fast path calls
 *   search_code on every single turn (app/agent/loop.py::_fast_path_calls),
 *   so "what's our refund policy" still comes back with a pile of weakly
 *   matching code the answer never used. Rendering that made the panel fill
 *   with code for questions that were not about code, and it is also how a
 *   junk chunk ("file x, 1 line, }") reached the screen — the model never
 *   cites those, it was the panel showing them anyway.
 * - It does not dedupe by chunk. Overlapping or adjacent chunks of one
 *   file collapse into a single region, because a stack of near-identical
 *   fragments of authController.ts is not a record of anything. Regions
 *   FAR apart stay separate: merging a citation at line 223 with one at
 *   line 1204 would claim the agent cited a thousand lines it never read,
 *   and then show only the first few hundred characters of it.
 *
 * Read-only on purpose: this is what the answers were grounded in, not a
 * scratchpad.
 */
export default function CodePanel({ turns }: { turns: { result?: AgentAnswer }[] }) {
  const cited = collectCitedFiles(turns);
  const [files, setFiles] = useState<Record<string, string>>({});

  const key = cited.map((c) => c.locator).join("|");
  useEffect(() => {
    let live = true;
    for (const item of cited) {
      if (!item.repoId) continue;
      const { path, startLine } = parseLocator(item.locator);
      const endLine = endOf(item.locator);
      readFile(item.repoId, path, startLine ?? undefined, endLine ?? undefined)
        .then((text) => {
          // Late responses from a previous render must not overwrite the
          // current call's panel.
          if (live && text) setFiles((prev) => ({ ...prev, [item.locator]: text }));
        })
        .catch(() => {
          /* fall back to the chunk; a sidebar is not worth an error state */
        });
    }
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (cited.length === 0) {
    return (
      <div className="h-full overflow-y-auto px-5 py-4">
        <p className="mt-6 text-[13px] leading-relaxed l-t-2">
          No code cited yet. Ask something about the codebase and whatever the answer was
          grounded in appears here, with the real line numbers from the file — so someone can
          say “line 47” and everyone lands in the same place.
        </p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto px-5 py-4">
      <ul className="space-y-5">
        {cited.map((item) => (
          <li key={item.locator}>
            <CodeSnippet locator={item.locator} code={files[item.locator] ?? item.snippet} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function endOf(locator: string): number | null {
  const match = locator.match(/:L\d+-L?(\d+)$/);
  return match ? Number(match[1]) : null;
}

/** Two citations belong to the same region if they overlap or sit within
 *  this many lines of each other — close enough that one block of code
 *  reads as one place in the file, rather than two unrelated findings. */
const MERGE_GAP_LINES = 25;

/** Evidence ids the answer actually cited — from the inline [ev_xxx] markers
 *  and from the verified claims, which are the two places a citation is
 *  real. (The verifier strips claims whose ids are not in the turn's
 *  evidence, so claims are the stricter of the two and worth reading.) */
function citedEvidenceIds(answer?: AgentAnswer): Set<string> {
  const ids = new Set<string>();
  if (!answer) return ids;
  const re = new RegExp(CITATION_RE);
  let m: RegExpExecArray | null;
  while ((m = re.exec(answer.answer ?? ""))) {
    for (const id of citationIds(m[1])) ids.add(id);
  }
  for (const claim of answer.claims ?? []) {
    for (const id of claim.evidence_ids ?? []) ids.add(id);
  }
  return ids;
}

/** Cited code evidence, grouped into contiguous regions per file. */
function collectCitedFiles(turns: { result?: AgentAnswer }[]): Cited[] {
  type Region = { start: number; end: number; snippet: string; repoId: string | null };
  const byPath = new Map<string, Region[]>();

  for (const turn of turns) {
    const citedIds = citedEvidenceIds(turn.result);
    if (citedIds.size === 0) continue;
    for (const call of turn.result?.tool_trace ?? []) {
      // repo_id is read off the CALL, not the evidence item: the loop
      // forces it into the args of every repo-scoped tool, and evidence
      // items carry no repo of their own.
      const repoId = typeof call.args?.repo_id === "string" ? call.args.repo_id : null;

      for (const ev of (call.evidence ?? []) as Evidence[]) {
        if (ev.source_type !== "code") continue;
        // The panel's promise is "what the answers were grounded in", and
        // retrieved is not grounded — only cited is.
        if (!citedIds.has(ev.id)) continue;
        const { path, startLine } = parseLocator(ev.locator);
        if (startLine === null) continue;
        const end = endOf(ev.locator) ?? startLine;

        const regions = byPath.get(path) ?? [];
        // Merge only when the real file can be read back. Without a repo_id
        // the panel falls back to the CHUNK text, and a merged locator
        // (L42-L310) paired with one chunk's text would number that chunk
        // from line 42 — confident, wrong line numbers, which is the single
        // thing this panel exists to avoid. Unmerged, each chunk's locator
        // still describes exactly the text it holds.
        const near = repoId
          ? regions.find(
              (r) => startLine <= r.end + MERGE_GAP_LINES && end >= r.start - MERGE_GAP_LINES
            )
          : undefined;
        if (near) {
          near.start = Math.min(near.start, startLine);
          near.end = Math.max(near.end, end);
          // Keep the longest chunk as the fallback — it is the least
          // misleading thing to show if the file can't be read back.
          if (ev.snippet.length > near.snippet.length) near.snippet = ev.snippet;
          near.repoId = near.repoId ?? repoId;
        } else {
          regions.push({ start: startLine, end, snippet: ev.snippet, repoId });
        }
        byPath.set(path, regions);
      }
    }
  }

  const out: Cited[] = [];
  for (const [path, regions] of byPath) {
    for (const r of regions.sort((a, b) => a.start - b.start)) {
      out.push({ locator: `${path}:L${r.start}-L${r.end}`, snippet: r.snippet, repoId: r.repoId });
    }
  }
  return out;
}
