"use client";

/** My agent: the member's own page — who their agent is, what it can see,
 * what it asked them, and the Meet extension.
 *
 * Everything here is the member's alone. The profile shapes how the agent
 * speaks FOR them; the escalation log is the list of questions put to them
 * (nobody else's appear, and nobody else sees these); the extension pairs a
 * browser to their account only.
 */
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState, type KeyboardEvent } from "react";
import AppShell, { useWorkspace } from "../_ui/AppShell";
import { Badge, Card, Empty, ErrorNote, Field, PageHeader, timeAgo, type Tone } from "../_ui/kit";
import {
  createExtensionCode,
  getAgentProfile,
  getCallOptions,
  listExtensionDevices,
  myEscalations,
  revokeExtensionDevice,
  saveAgentProfile,
  type AgentProfile,
  type CallSource,
  type Escalation,
  type ExtensionDevice,
} from "@/lib/api";
import { INTEGRATIONS } from "@/lib/integrations";

const TABS = [
  ["profile", "Profile"],
  ["context", "What it can see"],
  ["asked", "Asked you"],
  ["extension", "Meet extension"],
] as const;
type TabKey = (typeof TABS)[number][0];

function urlTab(): TabKey {
  const t = typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("tab");
  return (TABS.find(([k]) => k === t)?.[0] ?? "profile") as TabKey;
}

// ── profile ──────────────────────────────────────────────────────────────

function TopicInput({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const t = draft.trim().toLowerCase();
    if (t && !value.includes(t)) onChange([...value, t]);
    setDraft("");
  };
  return (
    <div className="flex min-h-[32px] flex-wrap items-center gap-1.5 rounded-[7px] border bg-white px-2 py-1"
         style={{ borderColor: "var(--l-rule)" }}>
      {value.map((t) => (
        <span key={t} className="p-badge" data-tone="accent">
          {t}
          <button aria-label={`Remove ${t}`} onClick={() => onChange(value.filter((x) => x !== t))}>×</button>
        </span>
      ))}
      <input className="min-w-[120px] flex-1 bg-transparent py-0.5 outline-none" value={draft}
             placeholder={value.length ? "" : "pricing, roadmap, security…"}
             onChange={(e) => setDraft(e.target.value)} onBlur={add}
             onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
               if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
               if (e.key === "Backspace" && !draft && value.length) onChange(value.slice(0, -1));
             }} />
    </div>
  );
}

function ProfileTab() {
  const { workspace, me } = useWorkspace();
  const [p, setP] = useState<AgentProfile | null>(null);
  const [saved, setSaved] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAgentProfile().then(setP).catch((e) => setError(String(e)));
  }, []);
  if (!p) return error ? <ErrorNote>{error}</ErrorNote> : <p className="p-muted">Loading…</p>;

  const agent = workspace?.agent_name || "Photon";
  const name = p.display_name?.trim() || (me?.email ?? "you").split("@")[0];
  const update = (patch: Partial<AgentProfile>) => { setP({ ...p, ...patch }); setSaved("idle"); };
  const save = async () => {
    setSaved("saving");
    setError(null);
    try {
      setP(await saveAgentProfile(p));
      setSaved("saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSaved("idle");
    }
  };

  return (
    <div className="grid items-start gap-5 lg:grid-cols-[1fr_320px]">
      <Card title="How your agent represents you">
        <div className="space-y-4">
          <Field label="Your name, as it says it" hint="It always introduces itself as your agent — never as you.">
            <input className="p-input max-w-xs" value={p.display_name ?? ""} placeholder="Priya"
                   onChange={(e) => update({ display_name: e.target.value })} />
          </Field>
          <Field label="Always ask me about" hint="Topics or names it hands to you live on a call, even when it could answer. Enter to add.">
            <TopicInput value={p.always_escalate} onChange={(v) => update({ always_escalate: v })} />
          </Field>
          <Field label="It may commit to" hint="What it can promise on your behalf. Anything else becomes a follow-up for you.">
            <textarea className="p-textarea" rows={2} value={p.may_commit_to ?? ""}
                      placeholder="Scheduling follow-ups and sending docs. Never dates, discounts or custom work."
                      onChange={(e) => update({ may_commit_to: e.target.value })} />
          </Field>
          <Field label="Notes for your agent">
            <textarea className="p-textarea" rows={3} value={p.notes ?? ""}
                      placeholder="I own billing and the partner API. Loop in Dev for anything about the mobile app."
                      onChange={(e) => update({ notes: e.target.value })} />
          </Field>
          {error && <ErrorNote>{error}</ErrorNote>}
          <div className="flex items-center gap-3">
            <button className="p-btn" disabled={saved === "saving"} onClick={() => void save()}>
              {saved === "saving" ? "Saving…" : "Save"}
            </button>
            {saved === "saved" && <span className="p-hint">Saved</span>}
          </div>
        </div>
      </Card>
      <div className="space-y-5">
        <Card title="On a call it says">
          <p className="rounded-lg p-3 italic" style={{ background: "var(--l-paper-2)" }}>
            “Hi, I&apos;m {name}&apos;s {agent}, attending for {name} today. I&apos;ll answer what I can and check with {name} on
            anything I can&apos;t.”
          </p>
          <p className="mt-3 p-hint">
            When it can&apos;t answer something important it pings you — here and in Slack — and waits 60 seconds.
            {p.always_escalate.length > 0 && <> It always asks you about <b>{p.always_escalate.join(", ")}</b>.</>}
          </p>
        </Card>
        <Card title="Where it acts for you">
          <ul className="space-y-1.5 p-muted">
            <li><Link href="/agent" className="p-link">Tickets</Link> assigned to you and labelled <span className="p-code">photon-fix</span></li>
            <li><Link href="/call" className="p-link">Calls</Link> you set up as “My agent”</li>
            <li><Link href="/whisper" className="p-link">Whisper</Link> suggestions, only to you</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}

// ── context ──────────────────────────────────────────────────────────────

function ContextTab() {
  const { workspace, isOwner } = useWorkspace();
  const [sources, setSources] = useState<CallSource[] | null>(null);
  useEffect(() => {
    getCallOptions().then((o) => setSources(o.sources)).catch(() => setSources([]));
  }, []);
  const scopeOf = (key: string) => INTEGRATIONS.find((i) => i.key === key)?.scope;

  return (
    <div className="space-y-5">
      <Card title="What your agent can answer from" flush
            actions={<Link href="/dashboard" className="p-link text-[12px]">Manage knowledge</Link>}>
        {sources === null ? <p className="p-card-b p-muted">Loading…</p> : (
          <table className="p-table">
            <thead><tr><th>Source</th><th>Status</th><th>Who it&apos;s shared with</th></tr></thead>
            <tbody>
              {sources.filter((s) => !s.coming_soon).map((s) => {
                const scope = scopeOf(s.key);
                return (
                  <tr key={s.key}>
                    <td className="font-medium">{s.label}</td>
                    <td>
                      {s.available
                        ? <Badge tone={s.is_mock ? "info" : "ok"} dot>{s.is_mock ? "Mock data" : "Connected"}</Badge>
                        : <Badge>Not connected</Badge>}
                      <span className="ml-2 p-hint">{s.detail}</span>
                    </td>
                    <td className="p-muted">
                      {s.key === "past_calls" ? "Calls in this workspace"
                        : scope === "individual" ? "Only you"
                        : scope === "either" ? "You choose when connecting"
                        : `Everyone in ${workspace?.name ?? "the workspace"}`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="The rules it follows">
        <ul className="grid gap-2 p-muted md:grid-cols-2">
          <li>• Every claim cites a source — anything it can&apos;t cite is removed before you see it.</li>
          <li>• No answer is better than a guess: it abstains, or asks you.</li>
          <li>• It reads; it never writes to your tools — except the PR and ticket comment you approve.</li>
          <li>• Sources are scoped to this workspace; another workspace&apos;s data is never reachable.</li>
        </ul>
        {workspace?.org_agent_enabled && (
          <p className="mt-3 p-hint">
            This workspace also has a <b>company agent</b>{isOwner ? <> — its scope is set in <Link href="/admin" className="p-link">Admin</Link></> : ""}.
          </p>
        )}
      </Card>
    </div>
  );
}

// ── asked you ────────────────────────────────────────────────────────────

const ESC: Record<Escalation["status"], { label: string; tone: Tone }> = {
  open: { label: "Waiting on you", tone: "warn" },
  answered: { label: "Answered on the call", tone: "ok" },
  declined: { label: "You skipped it", tone: "neutral" },
  expired: { label: "Missed — follow up", tone: "bad" },
  answered_late: { label: "Answered late — follow up", tone: "warn" },
};

function AskedTab() {
  const [items, setItems] = useState<Escalation[] | null>(null);
  useEffect(() => {
    const load = () => myEscalations().then(setItems).catch(() => setItems([]));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);
  return (
    <Card title="Questions your agent put to you" flush>
      {items === null ? <p className="p-card-b p-muted">Loading…</p> : items.length === 0 ? (
        <Empty title="Nothing yet">
          When your agent can&apos;t answer something important on a call, it asks you — you&apos;ll get a notification
          and 60 seconds to reply. Every question lands here.
        </Empty>
      ) : (
        <table className="p-table">
          <thead><tr><th>When</th><th>Question</th><th>Outcome</th></tr></thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.id} className="p-row-hover">
                <td className="w-[150px] align-top">
                  <div>{timeAgo(e.created_at)}</div>
                  <div className="p-hint truncate">{e.meeting_title ?? "Call"}</div>
                </td>
                <td className="align-top">
                  <div className="font-medium">“{e.question}”</div>
                  {e.answer && <div className="mt-0.5 p-muted">You: {e.answer}</div>}
                  {(e.status === "expired" || e.status === "answered_late") && (
                    <div className="mt-0.5 p-hint">The agent promised a follow-up — reply to the client directly.</div>
                  )}
                </td>
                <td className="w-[220px] align-top">
                  <Badge tone={ESC[e.status].tone}>{ESC[e.status].label}</Badge>
                  <div className="mt-1 p-hint">{e.topic}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

// ── extension ────────────────────────────────────────────────────────────

function ExtensionTab() {
  const [pairing, setPairing] = useState<{ code: string; api_base: string; expires: number } | null>(null);
  const [devices, setDevices] = useState<ExtensionDevice[] | null>(null);
  const [left, setLeft] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listExtensionDevices().then(setDevices).catch(() => setDevices([]));
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!pairing) return;
    const t = setInterval(() => {
      const s = Math.max(0, Math.round((pairing.expires - Date.now()) / 1000));
      setLeft(s);
      if (s % 5 === 0) load(); // a freshly paired browser shows up without a refresh
    }, 1000);
    return () => clearInterval(t);
  }, [pairing, load]);

  const make = async () => {
    setError(null);
    try {
      const c = await createExtensionCode();
      setPairing({ code: c.code, api_base: c.api_base, expires: Date.now() + c.expires_in * 1000 });
      setLeft(c.expires_in);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="grid items-start gap-5 lg:grid-cols-[1fr_1fr]">
      <Card title="Pair a browser">
        <ol className="mb-4 list-decimal space-y-1 pl-5 p-muted">
          <li>Install the extension (<span className="p-code">photon/extension</span>, load unpacked).</li>
          <li>Open its side panel and paste the address and code below.</li>
          <li>Join a Google Meet with captions on — suggestions appear in the panel.</li>
        </ol>
        {pairing && left > 0 ? (
          <div className="rounded-lg p-3" style={{ background: "var(--l-paper-2)" }}>
            <div className="font-mono text-[22px] tracking-[0.2em]">{pairing.code}</div>
            <div className="mt-1 p-hint">Address <span className="p-code">{pairing.api_base}</span> · expires in {left}s</div>
          </div>
        ) : (
          <button className="p-btn" onClick={() => void make()}>Make a pairing code</button>
        )}
        {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
        <p className="mt-4 p-hint">
          The extension gets its own token: whisper only, this workspace only, revocable below. It never sees your password
          or your login, and no bot joins the call.
        </p>
      </Card>
      <Card title="Paired browsers" flush>
        {devices === null ? <p className="p-card-b p-muted">Loading…</p> : devices.length === 0 ? (
          <Empty title="None yet" />
        ) : (
          <table className="p-table">
            <tbody>
              {devices.map((d) => (
                <tr key={d.id}>
                  <td className="font-medium">{d.name}</td>
                  <td className="p-hint">paired {timeAgo(d.created_at)}{d.last_seen_at && ` · used ${timeAgo(d.last_seen_at)}`}</td>
                  <td className="text-right">
                    <button className="p-btn-danger" onClick={() => void revokeExtensionDevice(d.id).then(load)}>Disconnect</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function MyAgent() {
  const [tab, setTab] = useState<TabKey>(urlTab);
  const choose = (k: TabKey) => {
    setTab(k);
    window.history.replaceState(null, "", k === "profile" ? "/me" : `/me?tab=${k}`);
  };
  return (
    <>
      <PageHeader title="My agent" description="How your agent represents you, what it can see, and what it has asked you." />
      <div className="p-tabs mb-5" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" className="p-tab" aria-selected={tab === k} onClick={() => choose(k)}>{label}</button>
        ))}
      </div>
      {tab === "profile" && <ProfileTab />}
      {tab === "context" && <ContextTab />}
      {tab === "asked" && <AskedTab />}
      {tab === "extension" && <ExtensionTab />}
    </>
  );
}

function MyAgentRoute() {
  const params = useSearchParams();
  return <MyAgent key={params.get("tab") ?? ""} />;
}

export default function Page() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <MyAgentRoute />
      </Suspense>
    </AppShell>
  );
}
