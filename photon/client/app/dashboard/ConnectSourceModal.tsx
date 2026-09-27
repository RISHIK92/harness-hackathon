"use client";

import { useEffect, useState } from "react";
import {
  connectConnector,
  connectJira,
  deleteCustomDoc,
  getConnectorProviders,
  listConnectorResources,
  listCustomDocs,
  selectConnectorResources,
  startSlackInstall,
  uploadCustomDoc,
  type CustomDoc,
  type ProviderField,
} from "@/lib/api";

type ConnectorResourceRow = { id: string; name: string; selected: boolean };

/** One modal, per-source forms.
 *
 * The generic connectors (Linear, Notion, Datadog) render from the server's
 * own field list, so adding a provider needs no change here — the same
 * definition that validates the request describes the form, and the two
 * cannot drift apart.
 *
 * Jira, Slack and custom docs are hand-written because each is genuinely
 * different: a site URL plus an account, an OAuth redirect, and a file
 * upload are not variations of one form.
 */
export default function ConnectSourceModal({
  sourceKey,
  onClose,
  onConnected,
}: {
  sourceKey: string;
  onClose: () => void;
  onConnected: () => void;
}) {
  const [fields, setFields] = useState<ProviderField[] | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  // A generic connector isn't usable the instant credentials verify — it
  // still has to say WHICH of the vendor's resources to index (same
  // principle as Slack's channel picker: connecting must never silently
  // index everything). So the modal has a second step for these providers,
  // reached only after `connect` succeeds.
  const [connectionId, setConnectionId] = useState<string | null>(null);
  const [resources, setResources] = useState<ConnectorResourceRow[] | null>(null);
  const [resourceNote, setResourceNote] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const generic = ["linear", "notion", "datadog"].includes(sourceKey);
  const pickingResources = generic && connectionId !== null;

  // Already-added documents — the upload form used to be the only way to
  // see this source at all, with no way to tell what was already indexed
  // or to remove any one of them.
  const [docs, setDocs] = useState<CustomDoc[] | null>(null);
  const [docsError, setDocsError] = useState<string | null>(null);
  const [removingDoc, setRemovingDoc] = useState<string | null>(null);

  const refreshDocs = () => {
    listCustomDocs()
      .then(setDocs)
      .catch((e) => setDocsError(e instanceof Error ? e.message : String(e)));
  };

  useEffect(() => {
    if (!generic) return;
    getConnectorProviders()
      .then((all) => setFields(all[sourceKey]?.fields ?? []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [generic, sourceKey]);

  useEffect(() => {
    if (sourceKey !== "custom_docs") return;
    refreshDocs();
  }, [sourceKey]);

  const removeDoc = async (docId: string) => {
    setRemovingDoc(docId);
    setDocsError(null);
    try {
      await deleteCustomDoc(docId);
      setDocs((prev) => (prev ?? []).filter((d) => d.id !== docId));
      onConnected();
    } catch (e) {
      setDocsError(e instanceof Error ? e.message : String(e));
    } finally {
      setRemovingDoc(null);
    }
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (generic) {
        const credentials: Record<string, string> = {};
        const config: Record<string, string> = {};
        for (const f of fields ?? []) {
          (f.config ? config : credentials)[f.key] = values[f.key] ?? "";
        }
        const conn = await connectConnector(sourceKey, credentials, config);
        // Credentials verified — now ask which resources to index, rather
        // than closing the modal on a connection that syncs nothing.
        setConnectionId(conn.id);
        const { resources: available, note } = await listConnectorResources(conn.id);
        setResources(available);
        setResourceNote(note);
        setSelectedIds(new Set(available.filter((r) => r.selected).map((r) => r.id)));
        setBusy(false);
        return;
      } else if (sourceKey === "jira") {
        await connectJira(values.site_url ?? "", values.account_email ?? "", values.api_token ?? "");
      } else if (sourceKey === "slack") {
        const { url } = await startSlackInstall();
        window.location.href = url;
        return;
      } else if (sourceKey === "custom_docs") {
        const form = new FormData();
        if (values.title) form.append("title", values.title);
        form.append("text", values.text ?? "");
        const doc = await uploadCustomDoc(form);
        setDone(`Indexed “${doc.title}” into ${doc.chunk_count} chunks.`);
        setValues({});
        refreshDocs();
        onConnected();
        return;
      }
      onConnected();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleResource = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const saveResources = async () => {
    if (!connectionId) return;
    setBusy(true);
    setError(null);
    try {
      await selectConnectorResources(connectionId, Array.from(selectedIds));
      onConnected();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const label = sourceKey.replace(/_/g, " ");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="l-tokens l-scrim fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-6">
      <div className="l-sheet my-8 w-full max-w-lg p-6">
        <div className="flex items-start justify-between mb-3">
          <h2 className="text-[17px] capitalize">Connect {label}</h2>
        </div>

        {sourceKey === "slack" && (
          <p className="text-[13px] l-t-2 mb-4">
            You&apos;ll be taken to Slack to choose a workspace and which channels the bot can
            read. It only ever reads channels it has been added to, and asks for no write access.
          </p>
        )}

        {sourceKey === "custom_docs" && (
          <>
            {docs === null ? (
              <p className="text-[13px] l-t-muted mb-3">Loading added documents…</p>
            ) : docs.length > 0 ? (
              <div className="mb-4">
                <p className="text-[12px] tracking-[0.12em] uppercase l-t-muted mb-2">
                  Added ({docs.length})
                </p>
                <div className="max-h-48 overflow-y-auto flex flex-col gap-1">
                  {docs.map((d) => (
                    <div
                      key={d.id}
                      className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-[13px]"
                      style={{ background: "rgba(28,25,23,.03)" }}
                    >
                      <span className="truncate">
                        {d.title}
                        <span className="ml-2 text-[11px] l-t-muted">{d.chunk_count} chunks</span>
                      </span>
                      <button
                        onClick={() => removeDoc(d.id)}
                        disabled={removingDoc === d.id}
                        className="shrink-0 text-[11px] uppercase tracking-[0.1em] l-t-rust disabled:opacity-50"
                      >
                        {removingDoc === d.id ? "Removing…" : "Remove"}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-[13px] l-t-muted mb-3">No documents added yet.</p>
            )}
            {docsError && <p className="text-[13px] l-t-rust mb-3">{docsError}</p>}

            <p className="text-[13px] l-t-2 mb-3">
              Paste a business flow, runbook or escalation policy. Markdown headings become
              sections, so the agent can cite the right part rather than the whole document.
            </p>
            <input
              className="l-input mb-2"
              placeholder="Title (e.g. Support escalation policy)"
              value={values.title ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, title: e.target.value }))}
            />
            <textarea
              className="l-input mb-2 h-48 font-mono"
              placeholder={"# Refunds\nRefunds over $500 need finance approval…"}
              value={values.text ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, text: e.target.value }))}
            />
          </>
        )}

        {sourceKey === "jira" && (
          <div className="flex flex-col gap-2 mb-2">
            <Field label="Site URL" help="https://yourcompany.atlassian.net">
              <input
                className="l-input"
                placeholder="https://yourcompany.atlassian.net"
                value={values.site_url ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, site_url: e.target.value }))}
              />
            </Field>
            <Field label="Account email" help="The Atlassian account the API token belongs to">
              <input
                className="l-input"
                value={values.account_email ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, account_email: e.target.value }))}
              />
            </Field>
            <Field
              label="API token"
              help="id.atlassian.com → Security → API tokens. The connection sees exactly what you can see."
            >
              <input
                type="password"
                className="l-input"
                value={values.api_token ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, api_token: e.target.value }))}
              />
            </Field>
          </div>
        )}

        {generic && !pickingResources && (
          <div className="flex flex-col gap-2 mb-2">
            {(fields ?? []).map((f) => (
              <Field key={f.key} label={f.label} help={f.help}>
                <input
                  type={f.secret ? "password" : "text"}
                  className="l-input"
                  value={values[f.key] ?? ""}
                  onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                />
              </Field>
            ))}
            {fields === null && <p className="text-[13px] l-t-muted">Loading…</p>}
          </div>
        )}

        {pickingResources && (
          <div className="flex flex-col gap-2 mb-2">
            <p className="text-[13px] l-t-2">
              Credentials verified. Choose what the agent can read — nothing is indexed until
              you select it here.
            </p>
            {resourceNote && <p className="text-[12px] l-t-muted">{resourceNote}</p>}
            {resources === null && <p className="text-[13px] l-t-muted">Loading resources…</p>}
            {resources !== null && resources.length === 0 && !resourceNote && (
              <p className="text-[13px] l-t-muted">No resources found for this connection.</p>
            )}
            <div className="max-h-64 overflow-y-auto flex flex-col gap-1">
              {(resources ?? []).map((r) => (
                <label
                  key={r.id}
                  className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] hover:bg-[rgba(28,25,23,.04)]"
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(r.id)}
                    onChange={() => toggleResource(r.id)}
                  />
                  <span className="truncate">{r.name}</span>
                </label>
              ))}
            </div>
          </div>
        )}

        {error && <p className="text-[13px] l-t-rust mt-2">{error}</p>}
        {done && <p className="text-[13px] l-t-rust mt-2">{done}</p>}

        <div className="flex items-center gap-3 mt-4">
          <button
            onClick={pickingResources ? saveResources : submit}
            disabled={busy || (pickingResources && resources === null)}
            className="l-btn"
          >
            {busy
              ? "Checking…"
              : pickingResources
                ? `Save selection${selectedIds.size ? ` (${selectedIds.size})` : ""}`
                : sourceKey === "slack"
                  ? "Continue to Slack"
                  : "Connect"}
          </button>
          <button onClick={onClose} className="text-[12px] uppercase tracking-[0.14em] l-quiet">
            Cancel
          </button>
        </div>
        <p className="text-[11px] l-t-muted mt-3">
          Credentials are encrypted before they are stored, and are never returned by the API.
        </p>
      </div>
    </div>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-[12px] l-t-2">{label}</span>
      {children}
      {help && <span className="block text-[11px] l-t-muted mt-0.5">{help}</span>}
    </label>
  );
}
