"use client";

/** Admin: the workspace owner's page — the workspace, who is in it, what the
 * company agent may say, and whether the integrations actually work.
 *
 * Owner-only: the server enforces it on every call (403 otherwise); this page
 * simply does not render its controls for anyone else, rather than showing
 * buttons that fail on click.
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import AppShell, { useWorkspace } from "../_ui/AppShell";
import { Badge, Card, Empty, ErrorNote, Field, PageHeader, Stat, timeAgo } from "../_ui/kit";
import {
  changeMemberRole,
  decideJoinRequest,
  getAdminStatus,
  getCallOptions,
  getWorkspaceInvite,
  listJoinRequests,
  listWorkspaceMembers,
  removeMember,
  revokeWorkspaceInvite,
  rotateWorkspaceInvite,
  updateWorkspace,
  type AdminStatus,
  type CallSource,
  type JoinRequest,
  type WorkspaceMember,
  type WorkspaceRole,
} from "@/lib/api";

const TABS = [
  ["workspace", "Workspace"],
  ["members", "Members & access"],
  ["company", "Company agent"],
  ["integrations", "Integrations"],
] as const;
type TabKey = (typeof TABS)[number][0];

const ROLE_HELP: Record<WorkspaceRole, string> = {
  owner: "Everything, including members, integrations and the company agent",
  member: "Connects sources, hands tickets to their agent, starts calls",
  viewer: "Asks questions and joins calls; changes nothing",
};

function err(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

// ── workspace ────────────────────────────────────────────────────────────

function WorkspaceTab({ status }: { status: AdminStatus | null }) {
  const { workspace, reload } = useWorkspace();
  const [name, setName] = useState(workspace?.name ?? "");
  const [agentName, setAgentName] = useState(workspace?.agent_name ?? "");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setError(null);
    try {
      await updateWorkspace({ name, agent_name: agentName });
      setSaved(true);
      reload();
    } catch (e) {
      setError(err(e));
    }
  };
  const c = status?.counts;
  return (
    <div className="space-y-5">
      {c && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Members" value={c.members} />
          <Stat label="Repositories" value={c.repos_ready} hint="indexed" />
          <Stat label="PRs this week" value={c.prs_week} hint={`${c.jobs_open} tickets open`} />
          <Stat label="Missed questions" value={c.missed_week} hint={`of ${c.escalations_week} escalations this week`}
                tone={c.missed_week ? "bad" : undefined} />
        </div>
      )}
      <Card title="Workspace">
        <div className="grid max-w-2xl gap-4 sm:grid-cols-2">
          <Field label="Name"><input className="p-input" value={name} onChange={(e) => { setName(e.target.value); setSaved(false); }} /></Field>
          <Field label="What the agent is called" hint="Said out loud on calls, and the word people address it by.">
            <input className="p-input" value={agentName} placeholder="Photon" onChange={(e) => { setAgentName(e.target.value); setSaved(false); }} />
          </Field>
        </div>
        <p className="mt-3 p-hint">
          {workspace?.kind === "team"
            ? "Team workspace — everything connected is shared with every member's agent."
            : "Individual workspace — nothing here is shared."}
        </p>
        {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
        <div className="mt-4 flex items-center gap-3">
          <button className="p-btn" onClick={() => void save()}>Save</button>
          {saved && <span className="p-hint">Saved</span>}
        </div>
      </Card>
    </div>
  );
}

// ── members ──────────────────────────────────────────────────────────────

function MembersTab() {
  const { me } = useWorkspace();
  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [requests, setRequests] = useState<JoinRequest[]>([]);
  const [invite, setInvite] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listWorkspaceMembers().then(setMembers).catch((e) => setError(err(e)));
    listJoinRequests().then((r) => setRequests(r.filter((x) => x.status === "pending"))).catch(() => setRequests([]));
    getWorkspaceInvite().then((i) => setInvite(i.code)).catch(() => setInvite(null));
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const act = (fn: () => Promise<unknown>) => fn().then(load).catch((e) => setError(err(e)));

  return (
    <div className="space-y-5">
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="grid items-start gap-5 lg:grid-cols-[1fr_320px]">
        <Card title={`Members${members ? ` · ${members.length}` : ""}`} flush>
          {members === null ? <p className="p-card-b p-muted">Loading…</p> : (
            <table className="p-table">
              <thead><tr><th>Person</th><th>Role</th><th /></tr></thead>
              <tbody>
                {members.map((m) => {
                  const self = m.email === me?.email;
                  return (
                    <tr key={m.user_id}>
                      <td>
                        <div className="font-medium">{m.email}{self && <span className="ml-2 p-hint">you</span>}</div>
                        <div className="p-hint">joined {timeAgo(m.joined_at)}</div>
                      </td>
                      <td className="w-[170px]">
                        <select className="p-select" value={m.role} disabled={self} title={ROLE_HELP[m.role]}
                                onChange={(e) => void act(() => changeMemberRole(m.user_id, e.target.value as WorkspaceRole))}>
                          <option value="owner">Owner</option>
                          <option value="member">Member</option>
                          <option value="viewer">Viewer</option>
                        </select>
                      </td>
                      <td className="w-[150px] text-right">
                        {!self && (confirm === m.user_id ? (
                          <span className="inline-flex gap-1.5">
                            <button className="p-btn-danger" onClick={() => { setConfirm(null); void act(() => removeMember(m.user_id)); }}>Remove</button>
                            <button className="p-btn-quiet" onClick={() => setConfirm(null)}>Keep</button>
                          </span>
                        ) : (
                          <button className="p-btn-quiet" onClick={() => setConfirm(m.user_id)}>Remove…</button>
                        ))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>

        <div className="space-y-5">
          <Card title="Invite people">
            {invite ? (
              <>
                <div className="flex items-center gap-2">
                  <span className="p-code flex-1 truncate text-[14px]">{invite}</span>
                  <button className="p-btn-quiet" onClick={() => { void navigator.clipboard?.writeText(invite); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
                    {copied ? "Copied" : "Copy"}
                  </button>
                </div>
                <p className="mt-2 p-hint">Anyone with this code can ask to join — you approve each request.</p>
                <div className="mt-3 flex gap-2">
                  <button className="p-btn-quiet" onClick={() => void act(rotateWorkspaceInvite)}>New code</button>
                  <button className="p-btn-danger" onClick={() => void act(revokeWorkspaceInvite)}>Turn off</button>
                </div>
              </>
            ) : (
              <>
                <p className="p-muted">Invites are off.</p>
                <button className="p-btn mt-3" onClick={() => void act(rotateWorkspaceInvite)}>Create invite code</button>
              </>
            )}
          </Card>
          <Card title={`Requests to join${requests.length ? ` · ${requests.length}` : ""}`} flush>
            {requests.length === 0 ? <p className="p-card-b p-muted">None pending.</p> : (
              <ul>
                {requests.map((r) => (
                  <li key={r.id} className="flex items-center gap-2 border-b px-4 py-2.5 last:border-b-0" style={{ borderColor: "var(--l-rule)" }}>
                    <span className="min-w-0 flex-1 truncate">{r.email}</span>
                    <button className="p-btn" onClick={() => void act(() => decideJoinRequest(r.id, true))}>Approve</button>
                    <button className="p-btn-quiet" onClick={() => void act(() => decideJoinRequest(r.id, false))}>Decline</button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Roles">
            <dl className="space-y-2">
              {(Object.keys(ROLE_HELP) as WorkspaceRole[]).map((r) => (
                <div key={r}><dt className="font-medium capitalize">{r}</dt><dd className="p-hint">{ROLE_HELP[r]}</dd></div>
              ))}
            </dl>
          </Card>
        </div>
      </div>
    </div>
  );
}

// ── company agent ────────────────────────────────────────────────────────

function CompanyTab({ label }: { label: string }) {
  const { workspace, reload } = useWorkspace();
  const [sources, setSources] = useState<CallSource[] | null>(null);
  const [enabled, setEnabled] = useState(Boolean(workspace?.org_agent_enabled));
  // null = every shared source (the server default); a list narrows it.
  const [scope, setScope] = useState<string[] | null>(workspace?.org_agent_sources ?? null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCallOptions().then((o) => setSources(o.sources.filter((s) => s.available && !s.coming_soon))).catch(() => setSources([]));
  }, []);

  const allKeys = (sources ?? []).map((s) => s.key);
  const chosen = scope ?? allKeys;
  const toggle = (key: string) => {
    setSaved(false);
    setScope(chosen.includes(key) ? chosen.filter((k) => k !== key) : [...chosen, key]);
  };
  const save = async () => {
    setError(null);
    try {
      await updateWorkspace({ org_agent_enabled: enabled, ...(scope ? { org_agent_sources: scope } : {}) });
      setSaved(true);
      reload();
    } catch (e) {
      setError(err(e));
    }
  };

  return (
    <div className="grid items-start gap-5 lg:grid-cols-[1fr_320px]">
      <Card title="Company agent">
        <label className="flex cursor-pointer items-start gap-3">
          <input type="checkbox" className="mt-1 accent-[#b45309]" checked={enabled} onChange={(e) => { setEnabled(e.target.checked); setSaved(false); }} />
          <span>
            <span className="font-medium">Let an agent act for {workspace?.name ?? "the company"}</span>
            <span className="block p-muted">
              Calls can be attended as the company rather than as one person, and tickets labelled
              <span className="p-code mx-1">{label}</span>with no assignee are picked up by the company agent — with you approving its plans.
            </span>
          </span>
        </label>

        <div className="mt-5">
          <div className="p-eyebrow mb-2">What it may answer from</div>
          {sources === null ? <p className="p-muted">Loading…</p> : sources.length === 0 ? (
            <p className="p-muted">Nothing is connected yet. <Link href="/dashboard" className="p-link">Connect knowledge</Link> first.</p>
          ) : (
            <ul className="overflow-hidden rounded-lg border" style={{ borderColor: "var(--l-rule)", opacity: enabled ? 1 : 0.55 }}>
              {sources.map((s) => (
                <li key={s.key} className="border-b last:border-b-0" style={{ borderColor: "var(--l-rule)" }}>
                  <label className="flex cursor-pointer items-center gap-3 px-3 py-2">
                    <input type="checkbox" className="accent-[#b45309]" disabled={!enabled} checked={chosen.includes(s.key)} onChange={() => toggle(s.key)} />
                    <span className="font-medium">{s.label}</span>
                    <span className="truncate p-hint">{s.detail}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 p-hint">
            A company call starts with exactly these. Leave out anything internal — Slack threads and incident history are true,
            but not always for a customer&apos;s ears.
          </p>
        </div>
        {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
        <div className="mt-4 flex items-center gap-3">
          <button className="p-btn" onClick={() => void save()}>Save</button>
          {saved && <span className="p-hint">Saved</span>}
        </div>
      </Card>
      <Card title="Member agents vs company agent">
        <dl className="space-y-3">
          <div><dt className="font-medium">A member&apos;s agent</dt>
            <dd className="p-hint">Speaks as “Priya&apos;s Photon”, from Priya&apos;s access, and escalates to Priya.</dd></div>
          <div><dt className="font-medium">The company agent</dt>
            <dd className="p-hint">Speaks for the team, only from the sources you pick here, and escalates to whoever set up the call.</dd></div>
        </dl>
      </Card>
    </div>
  );
}

// ── integrations ─────────────────────────────────────────────────────────

function IntegrationsTab({ status, onRefresh }: { status: AdminStatus | null; onRefresh: () => void }) {
  return (
    <Card title="Integrations" actions={<button className="p-btn-quiet" onClick={onRefresh}>Recheck</button>} flush>
      {status === null ? <p className="p-card-b p-muted">Checking…</p> : (
        <table className="p-table">
          <tbody>
            {status.integrations.map((i) => (
              <tr key={i.key}>
                <td className="w-[240px] font-medium">{i.label}</td>
                <td className="w-[110px]">{i.ok ? <Badge tone="ok" dot>Working</Badge> : <Badge tone="warn" dot>Needs setup</Badge>}</td>
                <td className="p-muted">{i.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Admin() {
  const { workspace, isOwner } = useWorkspace();
  const [tab, setTab] = useState<TabKey>(() => {
    const t = typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("tab");
    return (TABS.find(([k]) => k === t)?.[0] ?? "workspace") as TabKey;
  });
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const loadStatus = useCallback(() => {
    getAdminStatus().then(setStatus).catch(() => setStatus(null));
  }, []);
  useEffect(() => {
    if (isOwner) loadStatus();
  }, [isOwner, loadStatus]);

  if (!workspace) return <p className="p-muted">Loading…</p>;
  if (!isOwner || workspace.is_personal) {
    return (
      <Card>
        <Empty title="Admin is for workspace owners">
          {workspace.is_personal ? "A personal workspace has nobody else to manage." : "Ask an owner if you need a change here."}
        </Empty>
      </Card>
    );
  }
  const choose = (k: TabKey) => {
    setTab(k);
    window.history.replaceState(null, "", k === "workspace" ? "/admin" : `/admin?tab=${k}`);
  };
  const needsSetup = status?.integrations.filter((i) => !i.ok).length ?? 0;

  return (
    <>
      <PageHeader title="Admin" description={`Manage ${workspace.name}: people, the company agent, and integrations.`} />
      <div className="p-tabs mb-5" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" className="p-tab" aria-selected={tab === k} onClick={() => choose(k)}>
            {label}
            {k === "integrations" && needsSetup > 0 && <span className="ml-1.5"><Badge tone="warn">{needsSetup}</Badge></span>}
          </button>
        ))}
      </div>
      {tab === "workspace" && <WorkspaceTab status={status} />}
      {tab === "members" && <MembersTab />}
      {tab === "company" && <CompanyTab label={status?.agent_fix_label ?? "photon-fix"} />}
      {tab === "integrations" && <IntegrationsTab status={status} onRefresh={() => { setStatus(null); loadStatus(); }} />}
    </>
  );
}

export default function Page() {
  return (
    <AppShell>
      <Admin />
    </AppShell>
  );
}
