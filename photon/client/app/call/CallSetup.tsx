"use client";

import { useCallback, useEffect, useState } from "react";
import {
  disableMock,
  enableMock,
  agentWorkerStatus,
  getCallOptions,
  getWorkspaceId,
  listWorkspaces,
  type CallOptions,
  type CallSource,
  type MockProvider,
} from "@/lib/api";
import Toggle from "../_ui/Toggle";
import AgentNameField from "./AgentNameField";

const MOCK_PROVIDERS = new Set<string>(["github", "slack", "jira", "linear", "notion", "datadog"]);

/** Chosen before joining: what the agent is for, which language, and which
 * sources it may use.
 *
 * Sources are shown even when unavailable, with a Connect action, because
 * "Jira isn't here" is more useful than Jira silently not existing — the
 * most common support question of all is "is this a known issue", and a
 * user who can't see that Jira is disconnected will assume the agent
 * checked and found nothing.
 */
export default function CallSetup({
  onStart,
  onConnectSource,
  busy,
}: {
  onStart: (config: {
    bot_types: string[];
    language_mode: string;
    enabled_sources: string[];
    attends_as: "member" | "company";
    mode: "speak" | "whisper";
  }) => void;
  onConnectSource: (sourceKey: string) => void;
  busy: boolean;
}) {
  const [options, setOptions] = useState<CallOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [botTypes, setBotTypes] = useState<string[]>(["support"]);
  const [language, setLanguage] = useState("english");
  const [sources, setSources] = useState<string[]>([]);
  const [showMore, setShowMore] = useState(false);
  const [mockBusy, setMockBusy] = useState<string | null>(null);
  // Who the agent attends FOR. "member" is your agent — "<you>'s Photon",
  // escalating to you. "company" represents the workspace and exists only
  // when an owner has enabled the org agent.
  const [attendsAs, setAttendsAs] = useState<"member" | "company">("member");
  // Speak: the agent answers aloud. Whisper: it transcribes everyone and
  // says nothing; suggestions reach each member privately.
  const [mode, setMode] = useState<"speak" | "whisper">("speak");
  const [orgAgent, setOrgAgent] = useState(false);
  // Whether a voice worker is alive right now. Starting a call with none
  // running used to produce a room with no agent and no explanation.
  const [worker, setWorker] = useState<{ online: boolean; detail?: string } | null>(null);
  useEffect(() => {
    const check = () => agentWorkerStatus().then(setWorker).catch(() => setWorker(null));
    check();
    const t = setInterval(check, 10_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    listWorkspaces()
      .then((all) => {
        const current = all.find((w) => w.id === getWorkspaceId()) ?? all.find((w) => w.is_personal);
        setOrgAgent(Boolean(current?.org_agent_enabled));
      })
      .catch(() => setOrgAgent(false));
  }, []);

  const loadOptions = useCallback(async (firstLoad: boolean) => {
    const o = await getCallOptions();
    setOptions(o);
    if (firstLoad) setSources(o.default_enabled);
  }, []);

  useEffect(() => {
    loadOptions(true).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [loadOptions]);

  const tryMock = async (key: MockProvider) => {
    setMockBusy(key);
    try {
      await enableMock(key);
      // GitHub's mock repo ingests asynchronously (clone/parse/embed, same
      // pipeline as any repo) — a few seconds, unlike the other providers
      // which are indexed synchronously before the response returns. Poll
      // briefly rather than leaving the row stuck on "Try mock".
      for (let i = 0; i < 6; i++) {
        const o = await getCallOptions();
        setOptions(o);
        if (key !== "github" || o.sources.find((s) => s.key === "github")?.available) break;
        await new Promise((r) => setTimeout(r, 1500));
      }
    } finally {
      setMockBusy(null);
    }
  };

  const turnOffMock = async (key: MockProvider) => {
    setMockBusy(key);
    try {
      await disableMock(key);
      setSources((prev) => prev.filter((k) => k !== key));
      await loadOptions(false);
    } finally {
      setMockBusy(null);
    }
  };

  if (error) return <p className="text-[color:var(--l-rust)] text-sm">{error}</p>;
  if (!options) return <p className="text-[color:var(--l-muted)] text-sm">Loading call options…</p>;

  const toggle = (list: string[], key: string) =>
    list.includes(key) ? list.filter((k) => k !== key) : [...list, key];

  // Primary vs "more" mirrors how often each is reached for on a support
  // call, not the order they were built in.
  const PRIMARY = ["github", "custom_docs", "slack", "jira"];
  const primary = options.sources.filter((s) => PRIMARY.includes(s.key));
  const secondary = options.sources.filter((s) => !PRIMARY.includes(s.key));
  // Nothing connected means the agent cannot answer anything, so starting a
  // call would produce a room where every question is met with an
  // abstention. The API refuses this too (409) — this is so the user finds
  // out before they invite anyone, not after.
  const nothingConnected = !options.sources.some((s) => s.available && !s.coming_soon);
  const internalCaution = botTypes.some(
    (k) => options.bot_types.find((b) => b.key === k)?.internal_caution
  );

  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <section>
        <h3 className="text-xs uppercase tracking-wide text-[color:var(--l-muted)] mb-2">
          What is this call for?
        </h3>
        <div className="flex flex-wrap gap-2">
          {options.bot_types.map((b) => {
            const on = botTypes.includes(b.key);
            return (
              <button
                key={b.key}
                onClick={() => setBotTypes((prev) => toggle(prev, b.key))}
                title={b.description}
                className={`rounded border px-3 py-1.5 text-sm ${
                  on
                    ? "border-[rgba(180,83,9,.45)] bg-[rgba(180,83,9,.07)] text-[color:var(--l-rust)]"
                    : "border-[color:var(--l-rule)] text-[color:var(--l-ink-2)] hover:text-[color:var(--l-ink)]"
                }`}
              >
                {b.label}
                {b.internal_caution && <span className="ml-1 text-[color:var(--l-terra)]">·internal</span>}
              </button>
            );
          })}
        </div>
        {internalCaution && (
          <p className="text-xs text-[color:var(--l-terra)] mt-2">
            Knowledge transfer allows internal detail — commercial terms, incident history,
            reasoning you would not say to a customer. The agent will flag it if a guest who
            isn&apos;t a workspace member is on the call, but don&apos;t pick this for a customer call.
          </p>
        )}
      </section>

      <section>
        <h3 className="text-xs uppercase tracking-wide text-[color:var(--l-muted)] mb-2">Language</h3>
        <div className="flex flex-wrap gap-2">
          {options.language_modes.map((m) => (
            <button
              key={m.key}
              onClick={() => setLanguage(m.key)}
              className={`rounded border px-3 py-1.5 text-sm text-left ${
                language === m.key
                  ? "border-[rgba(180,83,9,.45)] bg-[rgba(180,83,9,.07)] text-[color:var(--l-rust)]"
                  : "border-[color:var(--l-rule)] text-[color:var(--l-ink-2)] hover:text-[color:var(--l-ink)]"
              }`}
            >
              {m.label}
              <span className="block text-[10px] text-[color:var(--l-muted)]">{m.detail}</span>
            </button>
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-xs uppercase tracking-wide text-[color:var(--l-muted)] mb-2">
          Sources the agent may use
        </h3>
        <div className="flex flex-col gap-2">
          {primary.map((s) => (
            <SourceRow
              key={s.key}
              source={s}
              enabled={sources.includes(s.key)}
              onToggle={() => setSources((prev) => toggle(prev, s.key))}
              onConnect={() => onConnectSource(s.key)}
              canMock={MOCK_PROVIDERS.has(s.key)}
              mockBusy={mockBusy === s.key}
              onTryMock={() => tryMock(s.key as MockProvider)}
              onTurnOffMock={() => turnOffMock(s.key as MockProvider)}
            />
          ))}
        </div>

        {!showMore ? (
          <button
            onClick={() => setShowMore(true)}
            className="text-xs text-[color:var(--l-ink-2)] hover:text-[color:var(--l-ink)] mt-2"
          >
            More sources ({secondary.length})
          </button>
        ) : (
          <div className="flex flex-col gap-2 mt-2">
            {secondary.map((s) => (
              <SourceRow
                key={s.key}
                source={s}
                enabled={sources.includes(s.key)}
                onToggle={() => setSources((prev) => toggle(prev, s.key))}
                onConnect={() => onConnectSource(s.key)}
                canMock={MOCK_PROVIDERS.has(s.key)}
                mockBusy={mockBusy === s.key}
                onTryMock={() => tryMock(s.key as MockProvider)}
                onTurnOffMock={() => turnOffMock(s.key as MockProvider)}
              />
            ))}
          </div>
        )}
      </section>

      <section>
        <h3 className="text-xs uppercase tracking-wide text-[color:var(--l-muted)] mb-2">How it takes part</h3>
        <div className="flex flex-wrap gap-2">
          {([
            ["speak", "Speaks", "Answers questions out loud, and checks with you when it can't."],
            ["whisper", "Whispers", "Listens to everyone, says nothing. Suggestions reach you privately."],
          ] as const).map(([key, label, detail]) => (
            <button
              key={key}
              onClick={() => setMode(key)}
              className={`rounded border px-3 py-1.5 text-sm text-left ${
                mode === key
                  ? "border-[rgba(180,83,9,.45)] bg-[rgba(180,83,9,.07)] text-[color:var(--l-rust)]"
                  : "border-[color:var(--l-rule)] text-[color:var(--l-ink-2)] hover:text-[color:var(--l-ink)]"
              }`}
            >
              {label}
              <span className="block text-[10px] text-[color:var(--l-muted)]">{detail}</span>
            </button>
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-xs uppercase tracking-wide text-[color:var(--l-muted)] mb-2">Attends as</h3>
        <div className="flex flex-wrap gap-2">
          {([
            ["member", "My agent", "Introduces itself as your agent, answers from your context, checks with you live when it can't."],
            ["company", "The company", orgAgent ? "Represents the workspace from company-wide sources." : "An owner has to enable the org agent first."],
          ] as const).map(([key, label, detail]) => (
            <button
              key={key}
              disabled={key === "company" && !orgAgent}
              onClick={() => setAttendsAs(key)}
              className={`rounded border px-3 py-1.5 text-sm text-left disabled:opacity-50 ${
                attendsAs === key
                  ? "border-[rgba(180,83,9,.45)] bg-[rgba(180,83,9,.07)] text-[color:var(--l-rust)]"
                  : "border-[color:var(--l-rule)] text-[color:var(--l-ink-2)] hover:text-[color:var(--l-ink)]"
              }`}
            >
              {label}
              <span className="block text-[10px] text-[color:var(--l-muted)]">{detail}</span>
            </button>
          ))}
        </div>
      </section>

      <AgentNameField />

      {nothingConnected && (
        <div className="border border-[rgba(194,112,61,.4)] bg-[rgba(194,112,61,.06)] rounded p-3 text-sm">
          <p className="text-[color:var(--l-terra)]">Connect a source before starting a call</p>
          <p className="text-[color:var(--l-ink-2)] mt-1">
            The agent answers only from what you connect. With nothing indexed it would abstain
            from every question, which is worse than not starting.
          </p>
          <a href="/dashboard" className="inline-block mt-2 text-[color:var(--l-rust)] hover:text-[color:var(--l-rust)]">
            Go to sources →
          </a>
        </div>
      )}

      {worker && !worker.online && (
        <div className="border border-[rgba(194,112,61,.4)] bg-[rgba(194,112,61,.06)] rounded p-3 text-sm">
          <p className="text-[color:var(--l-terra)]">The voice worker isn&apos;t running</p>
          <p className="text-[color:var(--l-ink-2)] mt-1">
            You can still start the call, but the agent can&apos;t join until it is — run{" "}
            <code>scripts/dev.sh</code> (it starts the call-agent) — the agent joins this call on its own as soon as the worker is up.
          </p>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={() =>
            onStart({ bot_types: botTypes, language_mode: language, enabled_sources: sources, attends_as: attendsAs, mode })
          }
          disabled={busy || botTypes.length === 0 || nothingConnected}
          className="l-btn"
        >
          {busy ? "Starting…" : "Start call"}
        </button>
        <p className="text-xs text-[color:var(--l-muted)]">
          {nothingConnected
            ? "Nothing connected yet"
            : sources.length === 0
              ? "No sources selected — the agent will have nothing to answer from."
              : `${sources.length} source${sources.length === 1 ? "" : "s"} enabled`}
        </p>
      </div>
    </div>
  );
}

function SourceRow({
  source,
  enabled,
  onToggle,
  onConnect,
  canMock,
  mockBusy,
  onTryMock,
  onTurnOffMock,
}: {
  source: CallSource;
  enabled: boolean;
  onToggle: () => void;
  onConnect: () => void;
  /** Whether this source has a mock provider at all (github/slack/jira/
   * linear/notion/datadog) — custom_docs doesn't. */
  canMock: boolean;
  mockBusy: boolean;
  onTryMock: () => void;
  onTurnOffMock: () => void;
}) {
  if (source.coming_soon) {
    return (
      <div className="flex items-center gap-3 border border-[color:var(--l-rule)] rounded px-3 py-2 opacity-60">
        <span className="text-sm text-[color:var(--l-ink-2)] flex-1">{source.label}</span>
        <span className="text-[10px] text-[color:var(--l-muted)] border border-[color:var(--l-rule)] rounded px-1.5 py-0.5">
          soon
        </span>
      </div>
    );
  }

  return (
    <div
      className={`flex items-center gap-3 border rounded px-3 py-2 ${
        enabled && source.available ? "border-[rgba(180,83,9,.4)] bg-[rgba(180,83,9,.06)]" : "border-[color:var(--l-rule)]"
      }`}
    >
      <Toggle
        label={source.label}
        checked={enabled && source.available}
        disabled={!source.available}
        onChange={onToggle}
      />
      <span className="text-sm text-[color:var(--l-ink)] flex-1">
        {source.label}
        {source.is_mock && (
          <span className="ml-1.5 text-[10px] uppercase tracking-wide text-[color:var(--l-terra)]">
            mock
          </span>
        )}
        <span className="block text-[10px] text-[color:var(--l-muted)]">{source.detail}</span>
      </span>
      {!source.available && (
        <button
          onClick={onConnect}
          className="text-xs border border-[rgba(180,83,9,.45)] text-[color:var(--l-rust)] hover:bg-[rgba(180,83,9,.07)] rounded px-2 py-1"
        >
          Connect
        </button>
      )}
      {!source.available && canMock && (
        <button
          onClick={onTryMock}
          disabled={mockBusy}
          className="text-xs border border-[color:var(--l-rule)] text-[color:var(--l-ink-2)] hover:text-[color:var(--l-ink)] rounded px-2 py-1 disabled:opacity-50"
          title="Adds fictional [MOCK] data so the agent has something to answer from — not a real connection"
        >
          {mockBusy ? "adding…" : "Try mock"}
        </button>
      )}
      {source.available && source.is_mock && (
        <button
          onClick={onTurnOffMock}
          disabled={mockBusy}
          className="text-xs border border-[color:var(--l-rule)] text-[color:var(--l-ink-2)] hover:text-[color:var(--l-ink)] rounded px-2 py-1 disabled:opacity-50"
        >
          {mockBusy ? "removing…" : "Turn off"}
        </button>
      )}
    </div>
  );
}
