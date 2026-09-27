"use client";

/** Home: what needs you, what your agent did, what is left to set up.
 *
 * The first screen after sign-in used to be the source catalog — useful once,
 * then noise. What a member actually opens Photon for is the queue: a plan
 * waiting for approval, a fix promised on a call that needs a repo, a
 * question the agent could not answer in time.
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import AppShell, { useWorkspace } from "../_ui/AppShell";
import { Badge, Card, Empty, JobBadge, PageHeader, Stat, timeAgo } from "../_ui/kit";
import {
  getAgentProfile,
  listAgentJobs,
  listExtensionDevices,
  listRepos,
  myEscalations,
  type AgentJob,
  type Escalation,
} from "@/lib/api";

type Item = { key: string; title: string; context: string; badge: React.ReactNode; href: string; action: string; at: string };

function needsYou(jobs: AgentJob[], esc: Escalation[]): Item[] {
  const items: Item[] = [];
  for (const j of jobs) {
    const base = { key: j.id, title: j.title, badge: <JobBadge status={j.status} />, href: `/agent?job=${j.id}`, at: j.updated_at };
    if (j.status === "awaiting_approval")
      items.push({ ...base, context: `Plan ready · ${j.plan?.files.length ?? 0} file(s)${j.ticket_ref ? ` · ${j.ticket_ref}` : ""}`, action: "Review plan" });
    if (j.status === "draft") items.push({ ...base, context: "Promised on a call — pick the repo", action: "Confirm" });
    if (j.status === "escalated") items.push({ ...base, context: j.escalation_reason ?? "The agent stopped", action: "Decide" });
  }
  for (const e of esc) {
    if (e.status === "expired" || e.status === "answered_late")
      items.push({
        key: e.id, title: `“${e.question}”`, at: e.created_at, href: "/me?tab=asked", action: "Follow up",
        context: `${e.meeting_title ?? "A call"} · the agent promised a follow-up`,
        badge: <Badge tone="warn">Missed question</Badge>,
      });
  }
  return items.sort((a, b) => (a.at < b.at ? 1 : -1));
}

function Home() {
  const { workspace } = useWorkspace();
  const [jobs, setJobs] = useState<AgentJob[] | null>(null);
  const [esc, setEsc] = useState<Escalation[]>([]);
  const [setup, setSetup] = useState<{ repos: number; profile: boolean; extension: number } | null>(null);

  useEffect(() => {
    listAgentJobs().then(setJobs).catch(() => setJobs([]));
    myEscalations().then(setEsc).catch(() => setEsc([]));
    Promise.all([
      listRepos().catch(() => []),
      getAgentProfile().catch(() => null),
      listExtensionDevices().catch(() => []),
    ]).then(([repos, profile, devices]) =>
      setSetup({
        repos: repos.filter((r) => r.status.toLowerCase() === "ready").length,
        profile: Boolean(profile?.display_name),
        extension: devices.length,
      }),
    );
  }, []);

  const queue = needsYou(jobs ?? [], esc);
  // Fixed at mount: "last 7 days" does not need to tick while the page is open.
  const [week] = useState(() => Date.now() - 7 * 86400e3);
  const recent = (jobs ?? []).slice(0, 6);
  const openJobs = (jobs ?? []).filter((j) => ["draft", "planning", "awaiting_approval", "fixing"].includes(j.status)).length;
  const prs = (jobs ?? []).filter((j) => j.status === "pr_open").length;
  const askedWeek = esc.filter((e) => new Date(`${e.created_at}Z`).getTime() > week).length;

  const steps = setup && [
    { done: setup.repos > 0, label: "Connect knowledge", detail: "A repository, docs or Slack — your agent answers only from these.", href: "/dashboard" },
    { done: setup.profile, label: "Tell your agent about you", detail: "Your name, and the topics it must always hand to you.", href: "/me" },
    { done: setup.extension > 0, label: "Pair the Meet extension", detail: "Suggestions beside Google Meet, no bot in the call.", href: "/me?tab=extension" },
  ];
  const incomplete = steps?.filter((s) => !s.done) ?? [];

  return (
    <>
      <PageHeader
        title={workspace ? workspace.name : "Home"}
        description="What needs you, and what your agent has been doing."
        actions={
          <>
            <Link href="/agent?new=1" className="p-btn-quiet">New ticket</Link>
            <Link href="/whisper" className="p-btn-quiet">Whisper a meeting</Link>
            <Link href="/call" className="p-btn">Start a call</Link>
          </>
        }
      />

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Needs you" value={queue.length} tone={queue.length ? "bad" : undefined} />
        <Stat label="Open tickets" value={openJobs} />
        <Stat label="PRs opened" value={prs} hint="by your agent" />
        <Stat label="Asked you" value={askedWeek} hint="last 7 days" />
      </div>

      <div className="grid items-start gap-5 lg:grid-cols-[1fr_320px]">
        <div className="space-y-5">
          <Card title="Needs you" flush>
            {jobs === null ? (
              <p className="p-card-b p-muted">Loading…</p>
            ) : queue.length === 0 ? (
              <Empty title="Nothing is waiting on you">Plans to approve, fixes promised on calls and missed questions show up here.</Empty>
            ) : (
              <ul>
                {queue.map((i) => (
                  <li key={i.key} className="p-row-hover flex items-center gap-3 border-b px-4 py-2.5 last:border-b-0" style={{ borderColor: "var(--l-rule)" }}>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        {i.badge}
                        <span className="truncate font-medium">{i.title}</span>
                      </div>
                      <div className="mt-0.5 truncate p-hint">{i.context} · {timeAgo(i.at)}</div>
                    </div>
                    <Link href={i.href} className="p-btn-quiet shrink-0">{i.action}</Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Agent activity" actions={<Link href="/agent" className="p-link text-[12px]">All tickets</Link>} flush>
            {recent.length === 0 ? (
              <Empty title="No tickets yet">Hand one to your agent, label a GitHub or Linear issue <span className="p-code">photon-fix</span>, or promise a fix on a call.</Empty>
            ) : (
              <table className="p-table">
                <tbody>
                  {recent.map((j) => (
                    <tr key={j.id} className="p-row-hover">
                      <td className="w-[140px]"><JobBadge status={j.status} /></td>
                      <td className="max-w-0 truncate"><Link href={`/agent?job=${j.id}`} className="hover:underline">{j.title}</Link></td>
                      <td className="hidden w-[120px] p-hint sm:table-cell">{j.ticket_ref ?? ""}</td>
                      <td className="w-[80px] text-right p-hint">{timeAgo(j.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>

        <div className="space-y-5">
          {incomplete.length > 0 && (
            <Card title={`Set up · ${steps!.length - incomplete.length}/${steps!.length}`}>
              <ul className="space-y-3">
                {steps!.map((s) => (
                  <li key={s.label} className="flex gap-2.5">
                    <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px]"
                          style={s.done ? { background: "var(--p-ok)", color: "#fff" } : { border: "1px solid var(--l-rule)" }}>
                      {s.done ? "✓" : ""}
                    </span>
                    <div className="min-w-0">
                      {s.done ? <span className="p-muted line-through">{s.label}</span>
                        : <Link href={s.href} className="font-medium hover:underline">{s.label}</Link>}
                      {!s.done && <p className="p-hint">{s.detail}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <Card title="How your agent works">
            <ol className="space-y-2 p-muted">
              <li><b className="text-[color:var(--l-ink)]">Tickets</b> — it plans a fix; you approve or narrow the files; it opens the PR or hands it back.</li>
              <li><b className="text-[color:var(--l-ink)]">Calls</b> — it attends as <i>your</i> agent, and pings you when it can&apos;t answer (60s to reply).</li>
              <li><b className="text-[color:var(--l-ink)]">Whisper</b> — it listens to a meeting and suggests answers only to you.</li>
            </ol>
          </Card>
        </div>
      </div>
    </>
  );
}

export default function Page() {
  return (
    <AppShell>
      <Home />
    </AppShell>
  );
}
