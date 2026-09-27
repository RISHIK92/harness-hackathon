"use client";

import { useEffect, useState } from "react";
import {
  GITHUB_STATE_LABEL,
  disableMock,
  enableMock,
  getCallOptions,
  getGithubStatus,
  getMockStatus,
  type CallSource,
  type GithubStatus,
  type MockProvider,
} from "@/lib/api";
import { Badge, Card, type Tone } from "../_ui/kit";
import { INTEGRATIONS, SCOPE_LABEL, type Integration } from "@/lib/integrations";

const MOCK_PROVIDERS = new Set<string>(["github", "slack", "jira", "linear", "notion", "datadog"]);

/** What the agent can draw on, and what it will be able to — one row per
 * source, with its real connection state.
 *
 * The unbuilt ones are shown for a reason beyond roadmap theatre: the value
 * of this product is the union of its sources, and a dashboard listing one
 * source makes it look like a code search tool. Each entry says what it
 * unlocks as an answer nobody could get today.
 *
 * Rules rather than cards, and the scope stated ONCE rather than on every
 * tile: every live source is workspace-scoped, so repeating that seven times
 * was seven lines of noise hiding the one place it differs — the mailboxes,
 * which are private to the person who connects them. That distinction is the
 * whole reason scope is surfaced at all, and it reads better when it is the
 * only scope label on the screen.
 */
export default function SourcesGrid({
  githubConnected,
  onConnectGithub,
  onConnectSource,
  canConnect = true,
  canUseMock = true,
}: {
  githubConnected: number;
  onConnectGithub: () => void;
  onConnectSource: (key: string) => void;
  /** Connecting a source is OWNER-only server-side (require_role(OWNER) in
   * routers/connectors.py, github_app.py, jira.py, slack.py) — false disables
   * the tiles instead of letting a viewer/member click into a 403. */
  canConnect?: boolean;
  /** Enabling mock data is MEMBER-level server-side (routers/mock.py),
   * same as adding a repo — false disables the "try with mock data" links. */
  canUseMock?: boolean;
}) {
  // GitHub's label comes from the shared status rather than a local count,
  // so the card, the dialog and the call setup screen all say the same
  // thing about whether it is connected.
  const [github, setGithub] = useState<GithubStatus | null>(null);
  useEffect(() => {
    getGithubStatus().then(setGithub).catch(() => setGithub(null));
  }, [githubConnected]);

  // Which providers already have fictional [MOCK] data for this workspace
  // (server/app/routers/mock.py) — a workspace's own testing aid, never a
  // real connection, so it's tracked and shown separately from `connected`.
  const [mock, setMock] = useState<Record<string, boolean>>({});
  const [mockBusy, setMockBusy] = useState<string | null>(null);
  const refreshMock = () => getMockStatus().then(setMock).catch(() => {});
  useEffect(() => {
    refreshMock();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [githubConnected]);

  // A failure here used to escape as an unhandled rejection (the dev error
  // overlay in development, nothing at all in production) — it is shown under
  // the table instead.
  const [mockError, setMockError] = useState<string | null>(null);
  const withMock = async (key: MockProvider, fn: (k: MockProvider) => Promise<unknown>) => {
    setMockBusy(key);
    setMockError(null);
    try {
      await fn(key);
      await refreshMock();
    } catch (e) {
      setMockError(e instanceof Error ? e.message : String(e));
    } finally {
      setMockBusy(null);
    }
  };
  const tryMock = (key: MockProvider) => withMock(key, enableMock);
  const turnOffMock = (key: MockProvider) => withMock(key, disableMock);

  // Real connection state for every source, from the same catalog the call
  // setup and the agents use — the old grid only knew about GitHub.
  const [catalog, setCatalog] = useState<Record<string, CallSource>>({});
  useEffect(() => {
    getCallOptions()
      .then((o) => setCatalog(Object.fromEntries(o.sources.map((x) => [x.key, x]))))
      .catch(() => setCatalog({}));
  }, [githubConnected, mock]);

  const live = INTEGRATIONS.filter((i) => i.status === "live");
  const soon = INTEGRATIONS.filter((i) => i.status === "coming_soon");
  const connectable = (key: string) =>
    key === "github" ? onConnectGithub : () => onConnectSource(key);

  const state = (i: Integration): { label: string; tone: Tone } => {
    if (i.key === "github" && github) {
      return { label: GITHUB_STATE_LABEL[github.state].replace(/^./, (c) => c.toUpperCase()),
               tone: github.state === "ready" ? "ok" : github.state === "not_connected" ? "neutral" : "warn" };
    }
    const c = catalog[i.key];
    if (mock[i.key]) return { label: "Mock data", tone: "info" };
    if (c?.available) return { label: "Connected", tone: "ok" };
    return { label: "Not connected", tone: "neutral" };
  };

  return (
    <Card
      title="Sources"
      actions={!canConnect && <span className="p-hint">Only an owner can connect a new source</span>}
      flush
    >
      <table className="p-table">
        <thead><tr><th>Source</th><th>What it adds</th><th>Status</th><th /></tr></thead>
        <tbody>
          {live.map((i) => {
            const st = state(i);
            const mocked = MOCK_PROVIDERS.has(i.key) && mock[i.key];
            return (
              <tr key={i.key} className="p-row-hover">
                <td className="w-[150px] font-medium">{i.name}</td>
                <td className="p-muted">
                  {i.unlocks}
                  {catalog[i.key]?.detail && catalog[i.key].available && (
                    <span className="block p-hint">{catalog[i.key].detail}</span>
                  )}
                </td>
                <td className="w-[170px]"><Badge tone={st.tone} dot={st.tone === "ok"}>{st.label}</Badge></td>
                <td className="w-[210px] text-right">
                  <span className="inline-flex items-center gap-1.5">
                    {MOCK_PROVIDERS.has(i.key) && (mocked ? (
                      <button className="p-hint underline" disabled={mockBusy === i.key || !canUseMock}
                              onClick={() => turnOffMock(i.key as MockProvider)}>
                        {mockBusy === i.key ? "removing…" : "remove mock"}
                      </button>
                    ) : st.tone !== "ok" && (
                      <button className="p-hint underline" disabled={mockBusy === i.key || !canUseMock}
                              title="Adds fictional [MOCK] data so the agent has something to answer from — not a real connection"
                              onClick={() => tryMock(i.key as MockProvider)}>
                        {mockBusy === i.key ? "adding…" : "try mock"}
                      </button>
                    ))}
                    <button className="p-btn-quiet" onClick={connectable(i.key)} disabled={!canConnect}
                            title={canConnect ? undefined : "Only an owner can connect a new source"}>
                      {st.tone === "ok" ? "Manage" : "Connect"}
                    </button>
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {mockError && (
        <p className="border-t px-4 py-2.5 text-[12.5px]" style={{ borderColor: "var(--l-rule)", color: "var(--p-bad)" }}>
          {mockError}
        </p>
      )}
      <p className="border-t px-4 py-2.5 p-hint" style={{ borderColor: "var(--l-rule)" }}>
        Coming soon:{" "}
        {soon.map((i, n) => (
          <span key={i.key}>
            {i.name}{i.scope === "individual" && ` (${SCOPE_LABEL.individual})`}{n < soon.length - 1 ? ", " : ""}
          </span>
        ))}
      </p>
    </Card>
  );
}
