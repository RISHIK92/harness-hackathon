"use client";

/** Tickets: everything your agent is fixing, and the decisions it needs.
 *
 * List on the left, the selected ticket on the right. The agent plans first
 * and changes nothing until you decide: a plan shows the root cause and every
 * file the fix would touch, and unticking files NARROWS it (it can never add
 * one — a file the plan did not name is a re-plan, not an approval). After
 * the fix, a PR goes up only when the harness verified it and the diff stayed
 * inside what you approved; anything else comes back here with the reason.
 *
 * The same decision can be made on the ticket itself with /approve, /narrow
 * or /reject — both routes land on one server-side path.
 */
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import AppShell, { useWorkspace } from "../_ui/AppShell";
import { Badge, Card, Empty, ErrorNote, Field, JobBadge, PageHeader, SOURCE_LABEL, timeAgo } from "../_ui/kit";
import {
  approveAgentJob,
  confirmAgentJob,
  createAgentJob,
  discardAgentJob,
  harnessStatus,
  listAgentJobs,
  listRepos,
  rejectAgentJob,
  replanAgentJob,
  type AgentJob,
  type Repo,
} from "@/lib/api";

const POLL_MS = 4000;
const IN_FLIGHT = ["planning", "fixing"];
const FILTERS = {
  needs: { label: "Needs you", match: (j: AgentJob) => ["awaiting_approval", "draft", "escalated"].includes(j.status) },
  active: { label: "In progress", match: (j: AgentJob) => IN_FLIGHT.includes(j.status) },
  done: { label: "Done", match: (j: AgentJob) => ["pr_open", "rejected", "failed"].includes(j.status) },
  all: { label: "All", match: () => true },
} as const;
type FilterKey = keyof typeof FILTERS;

function urlParam(name: string): string | null {
  return typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get(name);
}

const fixable = (repos: Repo[]) => repos.filter((r) => r.source_url && r.status.toLowerCase() === "ready" && !r.is_mock);

// ── decisions ────────────────────────────────────────────────────────────

function PlanDecision({ job, onDone }: { job: AgentJob; onDone: (j: AgentJob) => void }) {
  const plan = job.plan!;
  const [chosen, setChosen] = useState<string[]>(plan.files);
  const [notes, setNotes] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const narrowed = chosen.length < plan.files.length;

  const run = async (fn: () => Promise<AgentJob>) => {
    setBusy(true);
    setError(null);
    try {
      onDone(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="p-eyebrow">Root cause</div>
        <p className="mt-1">{plan.root_cause}</p>
      </div>
      <div>
        <div className="p-eyebrow">Proposed change</div>
        <p className="mt-1">{plan.fix_description}</p>
      </div>
      <div>
        <div className="p-eyebrow">Scope — untick files to narrow the fix</div>
        <ul className="mt-2 overflow-hidden rounded-lg border" style={{ borderColor: "var(--l-rule)" }}>
          {plan.files.map((f) => (
            <li key={f} className="border-b last:border-b-0" style={{ borderColor: "var(--l-rule)" }}>
              <label className="flex cursor-pointer items-start gap-2.5 px-3 py-2 hover:bg-[rgba(28,25,23,.025)]">
                <input type="checkbox" className="mt-0.5 accent-[#b45309]" checked={chosen.includes(f)}
                       onChange={(e) => setChosen((c) => (e.target.checked ? [...c, f] : c.filter((x) => x !== f)))} />
                <span className="min-w-0">
                  <span className="p-code">{f}</span>
                  {plan.file_intents?.[f] && <span className="ml-2 p-muted">{plan.file_intents[f]}</span>}
                </span>
              </label>
            </li>
          ))}
        </ul>
        {plan.must_not.length > 0 && (
          <p className="mt-2 p-hint">Won&apos;t touch: {plan.must_not.map((f) => <span key={f} className="p-code mr-1">{f}</span>)}</p>
        )}
      </div>
      {plan.risks.length > 0 && (
        <div>
          <div className="p-eyebrow">Risks</div>
          <ul className="mt-1 list-disc pl-5">{plan.risks.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      <Field label="Notes for the fix" hint="Optional — e.g. keep the public API unchanged.">
        <textarea className="p-textarea" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Field>
      {error && <ErrorNote>{error}</ErrorNote>}
      {rejecting ? (
        <div className="flex gap-2">
          <input className="p-input" autoFocus placeholder="Why? (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="p-btn-danger" disabled={busy} onClick={() => void run(() => rejectAgentJob(job.id, reason))}>Reject</button>
          <button className="p-btn-quiet" onClick={() => setRejecting(false)}>Cancel</button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <button className="p-btn" disabled={busy || chosen.length === 0}
                  onClick={() => void run(() => approveAgentJob(job.id, { files: narrowed ? chosen : undefined, constraints: notes }))}>
            {narrowed ? `Approve ${chosen.length} of ${plan.files.length} files` : "Approve plan"}
          </button>
          <button className="p-btn-quiet" onClick={() => setRejecting(true)}>Reject…</button>
        </div>
      )}
    </div>
  );
}

function DraftDecision({ job, repos, onDone }: { job: AgentJob; repos: Repo[]; onDone: (j: AgentJob) => void }) {
  const usable = fixable(repos);
  const [repoId, setRepoId] = useState(usable[0]?.id ?? "");
  const [title, setTitle] = useState(job.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<AgentJob>) => {
    setBusy(true);
    setError(null);
    try {
      onDone(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3">
      <p className="p-muted">A fix was promised on a call. Nothing runs until you confirm it and pick the repository.</p>
      <pre className="whitespace-pre-wrap rounded-lg p-3 text-[12.5px]" style={{ background: "var(--l-paper-2)" }}>{job.issue_text}</pre>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Ticket title"><input className="p-input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label="Repository">
          <select className="p-select" value={repoId} onChange={(e) => setRepoId(e.target.value)}>
            {usable.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </Field>
      </div>
      {!usable.length && <p className="p-hint">No fixable repository yet — <Link href="/dashboard" className="p-link">connect GitHub</Link>.</p>}
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="flex gap-2">
        <button className="p-btn" disabled={busy || !repoId} onClick={() => void run(() => confirmAgentJob(job.id, { repo_id: repoId, title }))}>
          Confirm and plan
        </button>
        <button className="p-btn-quiet" disabled={busy} onClick={() => void run(() => discardAgentJob(job.id))}>Discard</button>
      </div>
    </div>
  );
}

function Outcome({ job }: { job: AgentJob }) {
  const r = job.result;
  return (
    <div className="space-y-4">
      {job.pr_url && (
        <div className="flex items-center gap-2 rounded-lg p-3" style={{ background: "var(--p-ok-bg)" }}>
          <Badge tone="ok">PR open</Badge>
          <a href={job.pr_url} target="_blank" rel="noreferrer" className="truncate font-medium hover:underline">{job.pr_url}</a>
        </div>
      )}
      {job.escalation_reason && job.status !== "pr_open" && (
        <div className="rounded-lg p-3" style={{ background: "var(--p-bad-bg)", color: "var(--p-bad)" }}>
          <div className="font-medium">Handed back to you</div>
          <div className="mt-0.5">{job.escalation_reason}</div>
        </div>
      )}
      <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5">
        {job.approved_scope && (
          <>
            <dt className="p-muted">Approved scope</dt>
            <dd>{job.approved_scope.files.map((f) => <span key={f} className="p-code mr-1">{f}</span>)}</dd>
          </>
        )}
        {r && (
          <>
            <dt className="p-muted">Harness result</dt><dd>{r.outcome ?? "—"}</dd>
            {r.confidence?.overall && (<><dt className="p-muted">Confidence</dt><dd>{r.confidence.overall} ({r.confidence.score}/6)</dd></>)}
            {r.scope_check && (<><dt className="p-muted">Scope check</dt>
              <dd>{r.scope_check.ok ? <Badge tone="ok">Inside approved scope</Badge> : <Badge tone="bad">Outside: {[...r.scope_check.outside, ...r.scope_check.forbidden].join(", ")}</Badge>}</dd></>)}
          </>
        )}
      </dl>
      {r?.diff && (
        <div>
          <div className="p-eyebrow mb-1">Diff</div>
          <pre className="p-pre max-h-[420px]">{r.diff}</pre>
        </div>
      )}
    </div>
  );
}

function Detail({ job, repos, onChange }: { job: AgentJob; repos: Repo[]; onChange: (j: AgentJob) => void }) {
  const repo = repos.find((r) => r.id === job.repo_id);
  const [error, setError] = useState<string | null>(null);
  return (
    <Card
      title={
        <div className="flex min-w-0 items-center gap-2">
          <JobBadge status={job.status} />
          <span className="truncate">{job.title}</span>
        </div>
      }
      actions={["escalated", "rejected", "failed", "pr_open"].includes(job.status) && (
        <button className="p-btn-quiet" onClick={() => void replanAgentJob(job.id).then(onChange).catch((e) => setError(String(e)))}>Re-plan</button>
      )}
    >
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 p-hint">
        <span>{SOURCE_LABEL[job.source] ?? job.source}</span>
        {job.ticket_ref && (job.ticket_url
          ? <a href={job.ticket_url} target="_blank" rel="noreferrer" className="p-link">{job.ticket_ref}</a>
          : <span>{job.ticket_ref}</span>)}
        {repo && <span>{repo.name}</span>}
        {job.acting_for === "company" && <Badge>Company agent</Badge>}
        <span>updated {timeAgo(job.updated_at)}</span>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      {job.status === "draft" && <DraftDecision key={job.id} job={job} repos={repos} onDone={onChange} />}
      {job.status === "planning" && <p className="p-muted">Reading the code and finding the root cause. The plan appears here — nothing is changed until you approve it.</p>}
      {job.status === "fixing" && <p className="p-muted">Fixing inside the approved scope, then running the repository&apos;s own tests.</p>}
      {job.status === "awaiting_approval" && job.plan && <PlanDecision key={job.id} job={job} onDone={onChange} />}
      {!["draft", "planning", "awaiting_approval", "fixing"].includes(job.status) && <Outcome job={job} />}
    </Card>
  );
}

// ── new ticket ───────────────────────────────────────────────────────────

function NewTicket({ repos, onClose, onCreated }: { repos: Repo[]; onClose: () => void; onCreated: (j: AgentJob) => void }) {
  const usable = fixable(repos);
  const [repoId, setRepoId] = useState(usable[0]?.id ?? "");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [ticketUrl, setTicketUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const m = ticketUrl.match(/github\.com\/([^/]+\/[^/]+)\/issues\/(\d+)/);
      onCreated(await createAgentJob({ repo_id: repoId, title: title.trim(), body,
        ticket_url: ticketUrl || undefined, ticket_ref: m ? `${m[1]}#${m[2]}` : undefined }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/25 p-4 pt-[10vh]">
      <button className="absolute inset-0 cursor-default" aria-label="Close" onClick={onClose} />
      <div className="p-card relative w-full max-w-lg shadow-xl">
        <div className="p-card-h"><span className="p-card-t">New ticket for your agent</span>
          <button className="p-hint" onClick={onClose}>Close</button></div>
        <div className="p-card-b space-y-3">
          {!usable.length ? (
            <p className="p-muted">Your agent fixes against a real clone — <Link href="/dashboard" className="p-link">connect a GitHub repository</Link> first.</p>
          ) : (
            <>
              <Field label="Repository">
                <select className="p-select" value={repoId} onChange={(e) => setRepoId(e.target.value)}>
                  {usable.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                </select>
              </Field>
              <Field label="Title"><input className="p-input" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Dashboard counts don't add up" /></Field>
              <Field label="What's wrong, and how to see it">
                <textarea className="p-textarea" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
              </Field>
              <Field label="GitHub issue" hint="Optional. The plan is also posted there, and you can approve from the issue.">
                <input className="p-input" value={ticketUrl} onChange={(e) => setTicketUrl(e.target.value)} placeholder="https://github.com/org/repo/issues/12" />
              </Field>
              {error && <ErrorNote>{error}</ErrorNote>}
              <div className="flex justify-end gap-2">
                <button className="p-btn-quiet" onClick={onClose}>Cancel</button>
                <button className="p-btn" disabled={busy || !title.trim() || !repoId} onClick={() => void submit()}>
                  {busy ? "Handing over…" : "Hand to my agent"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── page ─────────────────────────────────────────────────────────────────

function Tickets() {
  const router = useRouter();
  const { isOwner } = useWorkspace();
  const [jobs, setJobs] = useState<AgentJob[] | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [selected, setSelected] = useState<string | null>(() => urlParam("job"));
  const [filter, setFilter] = useState<FilterKey>("needs");
  const [creating, setCreating] = useState(() => urlParam("new") === "1");
  const [harness, setHarness] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The old tabs moved to My agent; keep their links working.
    const tab = urlParam("tab");
    if (tab === "escalations") router.replace("/me?tab=asked");
    if (tab === "profile") router.replace("/me");
  }, [router]);

  const refresh = useCallback(async () => {
    try {
      setJobs(await listAgentJobs());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    listAgentJobs().then(setJobs).catch((e) => setError(e instanceof Error ? e.message : String(e)));
    listRepos().then(setRepos).catch(() => setRepos([]));
    harnessStatus().then((h) => setHarness(h.reachable)).catch(() => setHarness(false));
  }, [refresh]);

  const busy = (jobs ?? []).some((j) => IN_FLIGHT.includes(j.status));
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [busy, refresh]);

  const counts = useMemo(() => Object.fromEntries(
    (Object.keys(FILTERS) as FilterKey[]).map((k) => [k, (jobs ?? []).filter(FILTERS[k].match).length]),
  ) as Record<FilterKey, number>, [jobs]);
  // Land on something useful: "Needs you" when there is anything, else everything.
  const effective: FilterKey = filter === "needs" && jobs && counts.needs === 0 ? "all" : filter;
  const visible = (jobs ?? []).filter(FILTERS[effective].match);
  const job = (jobs ?? []).find((j) => j.id === selected) ?? visible[0] ?? null;

  const upsert = (j: AgentJob) => {
    setJobs((all) => [j, ...(all ?? []).filter((x) => x.id !== j.id)]);
    setSelected(j.id);
  };

  return (
    <>
      <PageHeader
        title="Tickets"
        description="Your agent plans a fix, you decide the scope, and it opens the PR — or hands it back with the reason."
        actions={
          <>
            <Badge tone={harness ? "ok" : harness === false ? "bad" : "neutral"} dot>
              Fix engine {harness === null ? "…" : harness ? "online" : "offline"}
            </Badge>
            <button className="p-btn" onClick={() => setCreating(true)}>New ticket</button>
          </>
        }
      />
      {harness === false && (
        <ErrorNote>
          The fix engine isn&apos;t reachable, so new tickets can&apos;t be planned.{" "}
          {isOwner ? <Link href="/admin" className="underline">See Admin → Integrations</Link> : "Ask a workspace owner to start it."}
        </ErrorNote>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}

      <div className="grid items-start gap-5 lg:grid-cols-[340px_1fr]">
        <Card flush>
          <div className="p-tabs px-2" role="tablist">
            {(Object.keys(FILTERS) as FilterKey[]).map((k) => (
              <button key={k} role="tab" className="p-tab" aria-selected={effective === k} onClick={() => setFilter(k)}>
                {FILTERS[k].label} <span className="p-muted">{counts[k]}</span>
              </button>
            ))}
          </div>
          {jobs === null ? (
            <p className="p-card-b p-muted">Loading…</p>
          ) : visible.length === 0 ? (
            <Empty title="Nothing here">
              Label a GitHub or Linear issue <span className="p-code">photon-fix</span> and assign it to yourself, or create one.
            </Empty>
          ) : (
            <ul className="max-h-[70vh] overflow-y-auto">
              {visible.map((j) => (
                <li key={j.id}>
                  <button onClick={() => setSelected(j.id)}
                          className="block w-full border-b px-4 py-2.5 text-left last:border-b-0 hover:bg-[rgba(28,25,23,.025)]"
                          style={{ borderColor: "var(--l-rule)", background: job?.id === j.id ? "var(--l-paper-2)" : undefined }}>
                    <div className="truncate font-medium">{j.title}</div>
                    <div className="mt-1 flex items-center gap-2">
                      <JobBadge status={j.status} />
                      <span className="truncate p-hint">{j.ticket_ref ?? SOURCE_LABEL[j.source] ?? j.source}</span>
                      <span className="ml-auto shrink-0 p-hint">{timeAgo(j.updated_at)}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {job ? <Detail job={job} repos={repos} onChange={upsert} />
          : <Card><Empty title="No ticket selected" /></Card>}
      </div>

      {creating && (
        <NewTicket repos={repos} onClose={() => setCreating(false)}
                   onCreated={(j) => { setCreating(false); setFilter("all"); upsert(j); }} />
      )}
    </>
  );
}

/** Keyed on ?job= so following a link to another ticket while already on
 * this page (Home's queue, a notification, the ticket comment) selects it —
 * state initialised from the URL would otherwise keep the old one. */
function TicketsRoute() {
  const params = useSearchParams();
  return <Tickets key={`${params.get("job") ?? ""}:${params.get("new") ?? ""}`} />;
}

export default function Page() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <TicketsRoute />
      </Suspense>
    </AppShell>
  );
}
