"use client";

import { useEffect, useState } from "react";
import { getWorkspaceId, listWorkspaces, updateWorkspace } from "@/lib/api";

const DEFAULT_NAME = "Photon";

/** Name the agent, asked where it matters: on the way into a call.
 *
 * Not decoration — this is what the agent calls itself in every answer, what
 * it announces when it joins, and, the part that surprises people, the wake
 * word it answers to. Rename it to Ava and "Photon, what's the retry policy"
 * stops working. That is correct behaviour, and worth saying out loud in the
 * UI rather than letting someone discover it mid-call.
 *
 * The name belongs to the WORKSPACE, not to this call, so the copy says so:
 * everything about this screen is otherwise per-call, and silently making one
 * field permanent would be a trap.
 */
export default function AgentNameField() {
  const [name, setName] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = getWorkspaceId();
    if (!id) return;
    listWorkspaces()
      .then((all) => {
        const current = all.find((w) => w.id === id);
        setName(current?.agent_name ?? "");
        setSaved(current?.agent_name ?? null);
      })
      .catch(() => undefined);
  }, []);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateWorkspace({ agent_name: name.trim() });
      setSaved(updated.agent_name ?? null);
      setName(updated.agent_name ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const effective = name.trim() || DEFAULT_NAME;
  const dirty = (name.trim() || null) !== saved;

  return (
    <section>
      <h3 className="text-xs uppercase tracking-wide text-[color:var(--l-muted)] mb-2">
        What it is called
      </h3>
      <div className="flex items-center gap-2">
        <input
          className="l-input max-w-[14rem]"
          placeholder={DEFAULT_NAME}
          value={name}
          maxLength={40}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && dirty && save()}
        />
        <button onClick={save} disabled={busy || !dirty} className="l-btn">
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      <p className="mt-2 text-[12px] leading-relaxed text-[color:var(--l-muted)]">
        Callers say{" "}
        <span className="text-[color:var(--l-ink)]">&ldquo;{effective}, …&rdquo;</span> to
        address it, and it introduces itself with this name. Leave blank to use {DEFAULT_NAME}.
        This applies to every call in the workspace, not just this one.
      </p>
      {error && (
        <p
          className="mt-2 pl-3 text-[12px] text-[color:var(--l-ink-2)]"
          style={{ borderLeft: "1px solid var(--l-rust)" }}
        >
          {error}
        </p>
      )}
    </section>
  );
}
