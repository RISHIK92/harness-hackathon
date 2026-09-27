"use client";

import { useState } from "react";
import { languageFor, parseLocator, tokenizeLine, type TokenKind } from "@/lib/highlight";

const COLOR: Record<TokenKind, string> = {
  plain: "text-[color:var(--l-ink-2)]",
  keyword: "text-[#7c3aed]",
  string: "text-[color:var(--l-rust)]",
  comment: "text-[color:var(--l-muted)] italic",
  number: "text-[color:var(--l-terra)]",
  function: "text-[#0369a1]",
};

/** Code evidence, rendered as code.
 *
 * Two details make this usable mid-call rather than merely prettier:
 *
 * - Line numbers come from the LOCATOR, not from 1. The agent cites
 *   `pricing.py:L42-L58`, so the panel shows 42-58 and someone can say
 *   "line 47" and have everyone land in the same place in their own editor.
 * - Code never wraps. Wrapped code misaligns with its line numbers and
 *   turns indentation into noise, so the block scrolls horizontally on its
 *   own instead of stretching the panel.
 */
export default function CodeSnippet({
  locator,
  code,
  collapsedLines = 12,
}: {
  locator: string;
  code: string;
  collapsedLines?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const { path, startLine } = parseLocator(locator);
  const language = languageFor(path);
  const lines = code.replace(/\s+$/, "").split("\n");
  const hidden = Math.max(0, lines.length - collapsedLines);
  const shown = expanded ? lines : lines.slice(0, collapsedLines);

  const copy = async () => {
    try {
      // Raw code, without line numbers — the point of copying is to paste
      // it somewhere that runs it.
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked; the code is still selectable */
    }
  };

  return (
    <div className="border border-[color:var(--l-rule)] rounded-lg overflow-hidden bg-[color:var(--l-paper)]">
      <div className="flex items-center gap-2 px-2.5 py-1.5 border-b border-[color:var(--l-rule)] bg-[rgba(28,25,23,.03)]">
        <span className="text-[11px] font-mono text-[color:var(--l-ink-2)] truncate flex-1" title={locator}>
          {path}
          {startLine !== null && <span className="text-[color:var(--l-muted)]">:{startLine}</span>}
        </span>
        {language && <span className="text-[10px] text-[color:var(--l-muted)]">{language}</span>}
        <button
          onClick={copy}
          className="text-[10px] text-[color:var(--l-muted)] hover:text-[color:var(--l-ink)] shrink-0"
        >
          {copied ? "copied" : "copy"}
        </button>
      </div>

      <div className="overflow-x-auto">
        <pre className="text-[11px] leading-[1.5] py-1.5 font-mono">
          {shown.map((line, i) => (
            <div key={i} className="flex hover:bg-[rgba(28,25,23,.03)] px-2">
              <span className="select-none text-[color:var(--l-muted)] text-right pr-3 w-10 shrink-0 tabular-nums">
                {startLine !== null ? startLine + i : i + 1}
              </span>
              <code className="whitespace-pre">
                {tokenizeLine(line, language).map((token, k) => (
                  <span key={k} className={COLOR[token.kind]}>
                    {token.text}
                  </span>
                ))}
              </code>
            </div>
          ))}
        </pre>
      </div>

      {hidden > 0 && (
        <button
          onClick={() => setExpanded((e) => !e)}
          className="w-full text-[11px] text-[color:var(--l-muted)] hover:text-[color:var(--l-ink)] py-1 border-t border-[color:var(--l-rule)]"
        >
          {expanded ? "show less" : `show ${hidden} more line${hidden === 1 ? "" : "s"}`}
        </button>
      )}
    </div>
  );
}
