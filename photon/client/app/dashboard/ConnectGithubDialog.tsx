"use client";

import { useEffect, useState } from "react";
import { getGithubAppInfo, getGithubStatus, type GithubAppInfo, type GithubStatus } from "@/lib/api";

/** Shown BEFORE handing the user off to GitHub.
 *
 * Installing a GitHub App is a decision with consequences that are hard to
 * see from GitHub's own screen: which account it lands on, which repos it
 * can read, and — for a private org — that the source will be cloned and
 * sent to third-party embedding and LLM providers. Worse, the most common
 * failure here is silent: a user-owned private App simply does not offer
 * the organisation as an install target, and people assume they picked the
 * wrong menu item.
 *
 * So this checks the App's real state via GitHub first and says
 * specifically what will happen, rather than showing generic instructions
 * the user has to map onto their own case.
 */
export default function ConnectGithubDialog({
  onCancel,
  onConfirm,
  busy,
}: {
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
}) {
  const [info, setInfo] = useState<GithubAppInfo | null>(null);
  const [status, setStatus] = useState<GithubStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);

  useEffect(() => {
    getGithubAppInfo()
      .then(setInfo)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // Where the user already is in the flow, so the dialog can tell them
    // the ONE next step instead of restating all of it every time.
    getGithubStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  const blockedForOrgs = info ? !info.org_install_supported : false;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="l-tokens l-scrim fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-6">
      <div className="l-sheet my-8 w-full max-w-2xl p-6">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h2 className="text-[19px]">Connect GitHub</h2>
            <p className="text-[13px] l-t-2">
              Here&apos;s what happens, before you leave for GitHub.
            </p>
          </div>
        </div>

        {error && <p className="text-[13px] l-t-rust mb-4">{error}</p>}
        {!info && !error && <p className="text-[13px] l-t-muted">Checking the app…</p>}

        {info && (
          <>
            {blockedForOrgs && (
              <div
                className="mb-4 rounded-xl p-4 text-[13px]"
                style={{ background: "rgba(180,83,9,.05)", border: "1px solid rgba(180,83,9,.28)" }}
              >
                <p className="mb-1 l-t-rust">
                  Installing on an organization needs one change first
                </p>
                <p className="l-t-2">
                  This app is owned by{" "}
                  <span className="font-mono">@{info.owner_login}</span> (a personal account) and is
                  private, so GitHub will only offer that account as an install target — an
                  organization like a private company org won&apos;t appear in the list.
                </p>
                <p className="l-t-2 mt-2">To install it on an organization, either:</p>
                <ul className="list-disc ml-5 mt-1 l-t-2 space-y-1">
                  <li>
                    <span className="l-t-ink">Transfer it to the org</span> — keeps it
                    private and correctly scoped. You must be an owner of that org.
                  </li>
                  <li>
                    <span className="l-t-ink">Make it public</span> — quicker, but then
                    anyone with the link can install it.
                  </li>
                </ul>
                <a
                  href={`${info.settings_url}#advanced`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-block mt-2 l-t-rust"
                >
                  Open app settings → Advanced ↗
                </a>
                <p className="l-t-muted mt-2 text-xs">
                  Installing on your personal account works right now without any of this.
                </p>
              </div>
            )}

            {status && status.state !== "not_connected" && (
              <div className="border border-emerald-700/50 bg-emerald-500/5 rounded p-3 mb-4 text-sm">
                <p className="text-emerald-300">
                  {status.state === "ready"
                    ? `Already connected — ${status.access.repos_indexed} repo${status.access.repos_indexed === 1 ? "" : "s"} indexed.`
                    : status.state === "installed_no_repos"
                      ? "The app is installed. Nothing is indexed yet — pick repositories below."
                      : `Signed in as @${status.identity.login} — the app still needs to be installed to read code.`}
                </p>
                {status.access.installations.length > 0 && (
                  <p className="text-neutral-400 mt-1">
                    Installed on{" "}
                    {status.access.installations.map((i) => `@${i.account}`).join(", ")}. Adding
                    another account or org is fine — they stack.
                  </p>
                )}
              </div>
            )}

            <ol className="space-y-3 text-sm mb-4">
              <Step n={1} title="Choose where to install">
                GitHub will ask which account or organization. Only accounts you own (or can
                request access to) appear.
              </Step>
              <Step n={2} title="Choose which repositories">
                Pick <span className="l-t-ink">Only select repositories</span> rather than
                all — you can add more later without reinstalling, and the picker here always shows
                the current list.
              </Step>
              <Step n={3} title="Approve the permissions">
                This app asks for{" "}
                <span className="font-mono l-t-ink">
                  {Object.entries(info.permissions)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(", ")}
                </span>
                . Read-only, and no write access to anything.
              </Step>
              <Step n={4} title="Come back and pick repos to index">
                You&apos;ll land back here with the repo list, an estimate of how long indexing
                takes, and nothing imported until you choose.
              </Step>
            </ol>

            <div className="l-hair mb-4 rounded-xl border p-4 text-[13px]">
              <p className="mb-1 l-t-ink">What happens to the code</p>
              <p className="l-t-2">
                Selected repos are cloned to this server, parsed, and split into chunks. Those
                chunks are sent to the embedding provider to build the search index, and relevant
                snippets are sent to the language model when someone asks a question. For a private
                or proprietary codebase, that&apos;s a deliberate decision worth making consciously.
              </p>
            </div>

            <label className="flex items-start gap-2 text-[13px] l-t-2 mb-4">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                className="mt-1"
              />
              <span>I understand where the code is sent, and I&apos;m choosing the repositories.</span>
            </label>

            <div className="flex items-center gap-3">
              <button
                onClick={onConfirm}
                disabled={!acknowledged || busy}
                className="l-btn"
              >
                {busy ? "Opening GitHub…" : "Continue to GitHub"}
              </button>
              <button
                onClick={onCancel}
                className="text-[12px] uppercase tracking-[0.14em] l-quiet"
              >
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span
        className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] l-t-rust"
        style={{ border: "1px solid rgba(180,83,9,.35)" }}
      >
        {n}
      </span>
      <span>
        <span className="l-t-ink">{title}</span>
        <span className="l-t-2"> — {children}</span>
      </span>
    </li>
  );
}
