/** The source catalog shown on the dashboard.
 *
 * Deliberately carries `scope` for every entry, including the ones that
 * aren't built yet: whether a connection belongs to the whole workspace or
 * to one person is the decision that is expensive to change later (it is
 * already in the DB as ConnectionScope), and showing it now sets the
 * expectation before anyone connects a mailbox and is surprised that
 * colleagues can query it.
 */
export type IntegrationStatus = "live" | "coming_soon";
export type IntegrationScope = "workspace" | "individual" | "either";

export type Integration = {
  key: string;
  name: string;
  /** What the agent gains — phrased as an answer it could not give before. */
  unlocks: string;
  status: IntegrationStatus;
  scope: IntegrationScope;
};

export const INTEGRATIONS: Integration[] = [
  {
    key: "github",
    name: "GitHub",
    unlocks: "Why the code does what it does — commits, PRs and blame behind a behaviour",
    status: "live",
    scope: "workspace",
  },
  {
    // Present in the connect modal and in CallSetup's primary list from the
    // start, but missing here — so nothing on the dashboard could open it.
    key: "custom_docs",
    name: "Custom docs",
    unlocks: "The escalation policy or business flow that lives in nobody's tool",
    status: "live",
    scope: "workspace",
  },
  {
    key: "slack",
    name: "Slack",
    unlocks: "The decision that never made it into a doc — the thread where it was agreed",
    status: "live",
    scope: "workspace",
  },
  {
    key: "jira",
    name: "Jira",
    unlocks: "Whether this is already a known issue, and who owns it",
    status: "live",
    scope: "workspace",
  },
  {
    key: "notion",
    name: "Notion",
    unlocks: "Runbooks and internal docs that aren't in the repo",
    status: "live",
    scope: "workspace",
  },
  {
    key: "linear",
    name: "Linear",
    unlocks: "What was decided on an issue, and why it was or wasn't fixed",
    status: "live",
    scope: "either",
  },
  {
    key: "datadog",
    name: "Datadog",
    unlocks: "Whether a monitor is alerting right now that explains what they're seeing",
    status: "live",
    scope: "workspace",
  },
  {
    key: "confluence",
    name: "Confluence",
    unlocks: "Architecture decisions and specs written outside the codebase",
    status: "coming_soon",
    scope: "workspace",
  },
  {
    key: "zendesk",
    name: "Zendesk",
    unlocks: "Whether this customer has hit this before, and what was promised",
    status: "coming_soon",
    scope: "workspace",
  },
  {
    key: "pagerduty",
    name: "PagerDuty",
    unlocks: "Whether an incident is open right now that explains what they're seeing",
    status: "coming_soon",
    scope: "workspace",
  },
  {
    key: "outlook",
    name: "Outlook",
    unlocks: "What was actually promised to this customer, in writing",
    status: "coming_soon",
    scope: "individual",
  },
  {
    key: "gmail",
    name: "Gmail",
    unlocks: "The thread with the customer that never reached a ticket",
    status: "coming_soon",
    scope: "individual",
  },
];

export const SCOPE_LABEL: Record<IntegrationScope, string> = {
  workspace: "shared with the workspace",
  individual: "private to you",
  either: "your choice when connecting",
};
