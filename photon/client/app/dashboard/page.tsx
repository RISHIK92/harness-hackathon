"use client";

/** Knowledge: what the agents can answer from, and who it is shared with.
 *
 * Sources first as one compact list — connected, mock, or not yet — then the
 * repositories that have actually been read. Workspace switching, members and
 * invites used to live in this page's own header; they moved to the app frame
 * and to Admin, so this page is only about knowledge.
 *
 * GitHub's install hand-off, Slack's OAuth and the call page's
 * ?connect=<source>&return=<call> detour all still land here.
 */
import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Toggle from "../_ui/Toggle";
import AppShell, { useWorkspace } from "../_ui/AppShell";
import { Badge, Card, Empty, ErrorNote, PageHeader, timeAgo } from "../_ui/kit";
import ConnectGithubDialog from "./ConnectGithubDialog";
import SourcesGrid from "./SourcesGrid";
import ConnectSourceModal from "./ConnectSourceModal";
import {
  connectRepo,
  deleteRepo,
  estimateIngest,
  importGithubRepos,
  listGithubInstallations,
  listInstallationRepos,
  type IngestEstimate,
  listRepos,
  startGithubInstall,
  type GithubRepoOption,
  type Repo,
} from "@/lib/api";

// RepoStatus is uppercase on the wire (PENDING / INGESTING / READY /
// FAILED). Compared case-insensitively so a future enum rename to
// lowercase doesn't silently freeze the list at "pending" forever, which
// is exactly what happened the first time.
const ACTIVE = new Set(["pending", "ingesting"]);
const isActive = (status: string) => ACTIVE.has(status.toLowerCase());
const isReady = (status: string) => status.toLowerCase() === "ready";

export default function DashboardPage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <Dashboard />
      </Suspense>
    </AppShell>
  );
}

function RepoStatus({ status }: { status: string }) {
  const s = status.toLowerCase();
  if (s === "ready") return <Badge tone="ok" dot>Ready</Badge>;
  if (s === "failed") return <Badge tone="bad" dot>Failed</Badge>;
  return <Badge tone="info" dot>Indexing</Badge>;
}

function Dashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { workspace } = useWorkspace();
  const [repos, setRepos] = useState<Repo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [ghConnecting, setGhConnecting] = useState(false);
  const [ghInstallationId, setGhInstallationId] = useState<number | null>(null);
  const [ghEstimate, setGhEstimate] = useState<IngestEstimate | null>(null);
  // The install hand-off is gated behind a pre-flight dialog: it is the one
  // step that leaves our UI, grants access to source code, and is easy to
  // get wrong silently (a user-owned private app simply won't list the org).
  const [showConnectDialog, setShowConnectDialog] = useState(false);
  const [ghInstallCount, setGhInstallCount] = useState(0);
  // Which source's connect form is open, and where to return afterwards
  // (the call page sends ?connect=slack&return=/call?code=abcd-efgh so a
  // user who left a call mid-setup lands back in the same one).
  const [connectSource, setConnectSource] = useState<string | null>(null);
  const [returnTo, setReturnTo] = useState<string | null>(null);
  const [ghRepos, setGhRepos] = useState<GithubRepoOption[]>([]);
  const [ghSelected, setGhSelected] = useState<Set<number>>(new Set());
  const [ghBusy, setGhBusy] = useState(false);
  const [ghError, setGhError] = useState<string | null>(null);
  const [showUrlField, setShowUrlField] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

  const refreshRepos = useCallback(async () => {
    try {
      setRepos(await listRepos());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    (async () => {
      await refreshRepos();
      // Connected-source count on first paint, not only after returning
      // from a GitHub install redirect.
      try {
        setGhInstallCount((await listGithubInstallations()).length);
      } catch {
        /* the sources list just shows "Connect" instead of a count */
      }
    })();
  }, [refreshRepos]);

  // Ingestion runs in Celery and takes ~1 minute, so the list has to move on
  // its own — otherwise a repo sits at "pending" until someone reloads.
  useEffect(() => {
    if (!repos.some((r) => isActive(r.status))) return;
    const handle = setInterval(refreshRepos, 3000);
    return () => clearInterval(handle);
  }, [repos, refreshRepos]);

  const openRepoPicker = useCallback(async (installationId: number) => {
    setGhError(null);
    setGhBusy(true);
    try {
      const { repos: options } = await listInstallationRepos(installationId);
      setGhInstallationId(installationId);
      setGhRepos(options);
      setGhSelected(new Set(options.filter((r) => !r.already_imported).map((r) => r.id)));
    } catch (e) {
      setGhError(e instanceof Error ? e.message : String(e));
    } finally {
      setGhBusy(false);
    }
  }, []);

  // Re-estimate whenever the selection changes. Debounced because ticking
  // several boxes quickly would otherwise fire a request per click, and the
  // answer is a coarse range — it does not need to track every keystroke.
  useEffect(() => {
    const chosen = ghRepos.filter((r) => ghSelected.has(r.id) && !r.already_imported);
    if (chosen.length === 0) {
      // Clearing goes through the same timer as setting, so neither path
      // updates state synchronously from the effect body.
      const clear = setTimeout(() => setGhEstimate(null), 0);
      return () => clearTimeout(clear);
    }
    const handle = setTimeout(() => {
      estimateIngest({ size_kb: chosen.map((r) => r.size_kb) })
        .then(setGhEstimate)
        // An estimate is a convenience; failing to get one must not block
        // the import button.
        .catch(() => setGhEstimate(null));
    }, 250);
    return () => clearTimeout(handle);
  }, [ghRepos, ghSelected]);

  useEffect(() => {
    const source = searchParams.get("connect");
    const back = searchParams.get("return");
    // Deferred a frame rather than set straight from the effect body: these
    // open a dialog, so a cascading render on mount is exactly what we do
    // not want, and one frame is imperceptible for a modal.
    const id = requestAnimationFrame(() => {
      if (back) setReturnTo(back);
      if (source) {
        // GitHub has its own pre-flight dialog; everything else uses the
        // generic connect modal.
        if (source === "github") setShowConnectDialog(true);
        else setConnectSource(source);
      }
    });
    return () => cancelAnimationFrame(id);
  }, [searchParams]);

  // GitHub redirects back here after an installation with ?installation=connected
  // but no installation_id — the app may have multiple installations, so we
  // just open the picker for the most recently created one.
  // The picker used to carry a Close button. Escape replaces it — an inline
  // panel with no way out would otherwise sit there until a reload.
  useEffect(() => {
    if (ghInstallationId === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setGhInstallationId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ghInstallationId]);

  useEffect(() => {
    if (searchParams.get("installation") !== "connected") return;
    router.replace("/dashboard");
    (async () => {
      try {
        const installations = await listGithubInstallations();
        setGhInstallCount(installations.length);
        const latest = installations[installations.length - 1];
        if (latest) await openRepoPicker(latest.installation_id);
      } catch (e) {
        setGhError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [searchParams, router, openRepoPicker]);

  const connectGithub = async () => {
    setGhError(null);
    setGhConnecting(true);
    try {
      const { url } = await startGithubInstall();
      window.location.href = url;
    } catch (e) {
      setGhError(e instanceof Error ? e.message : String(e));
      setGhConnecting(false);
    }
  };

  const toggleGhRepo = (id: number) => {
    setGhSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const importSelected = async () => {
    if (ghInstallationId === null) return;
    const chosen = ghRepos.filter((r) => ghSelected.has(r.id) && !r.already_imported);
    if (chosen.length === 0) return;
    setGhBusy(true);
    setGhError(null);
    try {
      await importGithubRepos(ghInstallationId, chosen);
      await openRepoPicker(ghInstallationId);
      await refreshRepos();
    } catch (e) {
      setGhError(e instanceof Error ? e.message : String(e));
    } finally {
      setGhBusy(false);
    }
  };

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const source = url.trim();
    if (!source) return;
    setBusy(true);
    setError(null);
    try {
      const name = source.replace(/\.git$/, "").split("/").slice(-2).join("/");
      await connectRepo(name, source);
      setUrl("");
      setShowUrlField(false);
      await refreshRepos();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };


  // Mirrors the server's own gates (require_role(OWNER) on connecting a
  // source, require_role(MEMBER) on repo add/delete) so a viewer sees a
  // disabled control with a reason instead of a click that 403s.
  const role = workspace?.role?.toLowerCase();
  const canConnectSources = role === "owner";
  const canEditRepos = role === "owner" || role === "member";
  const shared = workspace?.kind === "team";
  // Re-read on every poll (repos refresh every 3s while one is indexing), so
  // the stuck hint appears without a reload.
  const [stuckBefore, setStuckBefore] = useState(0);
  useEffect(() => {
    const id = setTimeout(() => setStuckBefore(Date.now() - 3 * 60 * 1000), 0);
    return () => clearTimeout(id);
  }, [repos]);

  return (
    <>
      {connectSource && (
        <ConnectSourceModal
          sourceKey={connectSource}
          onClose={() => setConnectSource(null)}
          onConnected={() => {
            refreshRepos();
            if (returnTo) window.location.href = returnTo;
          }}
        />
      )}
      {showConnectDialog && (
        <ConnectGithubDialog busy={ghConnecting} onCancel={() => setShowConnectDialog(false)} onConfirm={connectGithub} />
      )}

      <PageHeader
        title="Knowledge"
        description={
          shared
            ? `What the agents in ${workspace?.name} answer from. Everything here is shared with every member — read-only, scoped to what you select.`
            : "What your agent answers from. Read-only, and scoped to what you select."
        }
      />
      {error && <ErrorNote>{error}</ErrorNote>}
      {ghError && <ErrorNote>{ghError}</ErrorNote>}

      {ghInstallationId !== null && (
        <Card
          className="mb-5"
          title="Choose repositories to import"
          actions={<button className="p-hint" onClick={() => setGhInstallationId(null)}>Close</button>}
        >
          {ghBusy && ghRepos.length === 0 ? (
            <p className="p-muted">Loading repositories…</p>
          ) : (
            <>
              <div className="mb-2 flex items-center gap-3 p-hint">
                <button onClick={() => setGhSelected(new Set(ghRepos.filter((r) => !r.already_imported).map((r) => r.id)))} className="underline">Select all</button>
                <button onClick={() => setGhSelected(new Set())} className="underline">Clear</button>
                <span>{ghSelected.size} selected</span>
              </div>
              <ul className="max-h-72 overflow-y-auto rounded-lg border" style={{ borderColor: "var(--l-rule)" }}>
                {ghRepos.map((r) => (
                  <li key={r.id} className="flex items-center gap-3 border-b px-3 py-2 last:border-b-0" style={{ borderColor: "var(--l-rule)" }}>
                    <Toggle label={`Import ${r.full_name}`} checked={r.already_imported || ghSelected.has(r.id)}
                            disabled={r.already_imported} onChange={() => toggleGhRepo(r.id)} />
                    <span className={`truncate ${r.already_imported ? "p-muted" : ""}`}>{r.full_name}</span>
                    {r.private && <Badge>private</Badge>}
                    {r.already_imported && <span className="ml-auto"><Badge tone="ok">imported</Badge></span>}
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button onClick={importSelected} disabled={ghBusy || ghSelected.size === 0} className="p-btn">
                  {ghBusy ? "Importing…" : `Import ${ghSelected.size} repo${ghSelected.size === 1 ? "" : "s"}`}
                </button>
                {ghEstimate && (
                  <span className="p-hint">
                    about {ghEstimate.range_human} to index
                    {ghEstimate.calibrated ? ` · from ${ghEstimate.sample_size} previous imports` : " · rough estimate"}
                  </span>
                )}
              </div>
            </>
          )}
        </Card>
      )}

      <div className="space-y-5">
        <SourcesGrid
          githubConnected={ghInstallCount}
          onConnectGithub={() => {
            setGhError(null);
            setShowConnectDialog(true);
          }}
          onConnectSource={(key) => setConnectSource(key)}
          canConnect={canConnectSources}
          canUseMock={canEditRepos}
        />

        <Card
          title={`Repositories${repos.length ? ` · ${repos.length}` : ""}`}
          actions={canEditRepos && (
            <button className="p-btn-quiet" onClick={() => setShowUrlField((v) => !v)}>
              {showUrlField ? "Cancel" : "Add a public repo"}
            </button>
          )}
          flush
        >
          {showUrlField && (
            <form onSubmit={add} className="flex flex-wrap items-center gap-2 border-b px-4 py-3" style={{ borderColor: "var(--l-rule)" }}>
              <input className="p-input max-w-md flex-1" placeholder="https://github.com/org/repo" value={url}
                     onChange={(e) => setUrl(e.target.value)} autoFocus />
              <button disabled={busy || !url.trim()} className="p-btn">{busy ? "Connecting…" : "Connect"}</button>
              <span className="w-full p-hint">Public repositories only — for private code, connect GitHub so access is scoped and revocable.</span>
            </form>
          )}
          {repos.length === 0 ? (
            <Empty title="Nothing indexed yet">
              Connect GitHub above and pick repositories — a mid-sized repository is cloned, parsed and embedded in about seventeen seconds.
            </Empty>
          ) : (
            <table className="p-table">
              <thead><tr><th>Repository</th><th>Size</th><th>Status</th><th /></tr></thead>
              <tbody>
                {repos.map((r) => (
                  <tr key={r.id} className="p-row-hover">
                    <td>
                      <div className="font-medium">{r.name}{r.is_mock && <span className="ml-2"><Badge tone="info">mock</Badge></span>}</div>
                      {!isReady(r.status) && (
                        <div className="p-hint">{r.error_message || "Cloning, parsing and embedding…"}</div>
                      )}
                      {/* An ingest takes well under a minute. Minutes of "Indexing" means
                          the job never ran — almost always no Celery worker, or one that
                          crashed — and without this the page just spins forever. */}
                      {isActive(r.status) && Date.parse(`${r.created_at}Z`) < stuckBefore && (
                        <div className="mt-0.5 text-[12px]" style={{ color: "var(--p-warn)" }}>
                          Taking much longer than usual — check that the Celery worker is running
                          (<span className="p-code">scripts/dev.sh status</span>).
                        </div>
                      )}
                    </td>
                    <td className="p-muted">
                      {isReady(r.status) ? `${r.file_count} files · ${r.function_count} functions` : "—"}
                      {r.ingest_seconds ? <span className="p-hint"> · {Math.round(r.ingest_seconds)}s</span> : null}
                    </td>
                    <td><RepoStatus status={r.status} /><span className="ml-2 p-hint">{timeAgo(r.created_at)}</span></td>
                    <td className="w-[150px] text-right">
                      {canEditRepos && (confirmRemove === r.id ? (
                        <span className="inline-flex gap-1.5">
                          <button className="p-btn-danger" onClick={async () => { setConfirmRemove(null); await deleteRepo(r.id); refreshRepos(); }}>Remove</button>
                          <button className="p-btn-quiet" onClick={() => setConfirmRemove(null)}>Keep</button>
                        </span>
                      ) : (
                        <button className="p-btn-quiet" onClick={() => setConfirmRemove(r.id)}>Remove…</button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </>
  );
}
