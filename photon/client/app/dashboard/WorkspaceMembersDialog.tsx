"use client";

import { useEffect, useState } from "react";
import {
  changeMemberRole,
  decideJoinRequest,
  getWorkspaceInvite,
  listJoinRequests,
  listWorkspaceMembers,
  removeMember,
  revokeWorkspaceInvite,
  rotateWorkspaceInvite,
  type JoinRequest,
  type WorkspaceMember,
  type WorkspaceRole,
} from "@/lib/api";

/** Members, pending requests, and the invite code — the client half of
 * server/app/routers/workspaces.py's invite/join/member endpoints, which
 * had no UI at all before this. Mutating actions (rotate/revoke invite,
 * decide a request, change a role, remove someone) are OWNER-only on the
 * server (403 otherwise), so they're gated the same way here rather than
 * showing a control that would just fail on click.
 */
export default function WorkspaceMembersDialog({
  workspaceName,
  workspaceKind,
  myRole,
  onClose,
}: {
  workspaceName: string;
  workspaceKind?: "individual" | "team";
  myRole: string;
  onClose: () => void;
}) {
  const isOwner = myRole === "owner";
  const isIndividual = workspaceKind === "individual";

  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [requests, setRequests] = useState<JoinRequest[] | null>(null);
  const [inviteCode, setInviteCode] = useState<string | null>(null);
  const [inviteLoaded, setInviteLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = async () => {
    try {
      const m = await listWorkspaceMembers();
      setMembers(m);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    if (isOwner && !isIndividual) {
      try {
        const [inv, reqs] = await Promise.all([getWorkspaceInvite(), listJoinRequests()]);
        setInviteCode(inv.code);
        setRequests(reqs);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setInviteLoaded(true);
      }
    } else {
      setInviteLoaded(true);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const doRotate = async () => {
    setBusy(true);
    setError(null);
    try {
      const { code } = await rotateWorkspaceInvite();
      setInviteCode(code);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const doRevoke = async () => {
    setBusy(true);
    setError(null);
    try {
      await revokeWorkspaceInvite();
      setInviteCode(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const doDecide = async (id: string, approve: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await decideJoinRequest(id, approve);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const doRoleChange = async (userId: string, role: WorkspaceRole) => {
    setBusy(true);
    setError(null);
    try {
      await changeMemberRole(userId, role);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const doRemove = async (userId: string) => {
    setBusy(true);
    setError(null);
    try {
      await removeMember(userId);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const copyInvite = () => {
    if (!inviteCode) return;
    navigator.clipboard.writeText(inviteCode).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="l-tokens l-scrim fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-6">
      <div className="l-sheet my-8 w-full max-w-lg p-6">
        <div className="flex items-start justify-between mb-1">
          <h2 className="text-[17px]">Members — {workspaceName}</h2>
        </div>

        {isIndividual && (
          <p className="text-[13px] l-t-2 mb-3">
            This is an individual workspace — private to you, no invites. Create a team
            workspace to share it.
          </p>
        )}

        {isOwner && !isIndividual && (
          <div className="mt-3 mb-4 rounded-lg p-3" style={{ background: "rgba(28,25,23,.03)" }}>
            <p className="text-[12px] tracking-[0.12em] uppercase l-t-muted mb-2">Invite code</p>
            {!inviteLoaded ? (
              <p className="text-[13px] l-t-muted">Loading…</p>
            ) : inviteCode ? (
              <div className="flex items-center gap-2">
                <code className="text-[14px] px-2 py-1 rounded" style={{ background: "rgba(28,25,23,.05)" }}>
                  {inviteCode}
                </code>
                <button onClick={copyInvite} className="text-[12px] uppercase tracking-[0.1em] l-quiet" disabled={busy}>
                  {copied ? "Copied" : "Copy"}
                </button>
                <button onClick={doRotate} className="text-[12px] uppercase tracking-[0.1em] l-quiet" disabled={busy}>
                  Rotate
                </button>
                <button onClick={doRevoke} className="text-[12px] uppercase tracking-[0.1em] l-t-rust" disabled={busy}>
                  Revoke
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <p className="text-[13px] l-t-muted">No active invite code.</p>
                <button onClick={doRotate} className="text-[12px] uppercase tracking-[0.1em] l-quiet" disabled={busy}>
                  Create one
                </button>
              </div>
            )}
          </div>
        )}

        {isOwner && !isIndividual && requests !== null && requests.length > 0 && (
          <div className="mb-4">
            <p className="text-[12px] tracking-[0.12em] uppercase l-t-muted mb-2">
              Waiting to join ({requests.length})
            </p>
            <div className="flex flex-col gap-1">
              {requests.map((r) => (
                <div key={r.id} className="flex items-center justify-between text-[13px] px-2 py-1.5 rounded-lg" style={{ background: "rgba(28,25,23,.03)" }}>
                  <span className="truncate">{r.email}</span>
                  <div className="flex items-center gap-3 shrink-0">
                    <button onClick={() => doDecide(r.id, true)} className="text-[12px] uppercase tracking-[0.1em] l-quiet" disabled={busy}>
                      Admit
                    </button>
                    <button onClick={() => doDecide(r.id, false)} className="text-[12px] uppercase tracking-[0.1em] l-t-rust" disabled={busy}>
                      Deny
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <p className="text-[12px] tracking-[0.12em] uppercase l-t-muted mb-2">
            Members{members ? ` (${members.length})` : ""}
          </p>
          {members === null && <p className="text-[13px] l-t-muted">Loading…</p>}
          <div className="flex flex-col gap-1">
            {(members ?? []).map((m) => (
              <div key={m.user_id} className="flex items-center justify-between text-[13px] px-2 py-1.5 rounded-lg">
                <span className="truncate">{m.email}</span>
                {isOwner ? (
                  <div className="flex items-center gap-2 shrink-0">
                    <select
                      value={m.role}
                      onChange={(e) => doRoleChange(m.user_id, e.target.value as WorkspaceRole)}
                      disabled={busy}
                      className="text-[12px] uppercase tracking-[0.08em] bg-transparent"
                    >
                      <option value="viewer">Viewer</option>
                      <option value="member">Member</option>
                      <option value="owner">Owner</option>
                    </select>
                    <button
                      onClick={() => doRemove(m.user_id)}
                      className="text-[12px] uppercase tracking-[0.1em] l-t-rust"
                      disabled={busy}
                    >
                      Remove
                    </button>
                  </div>
                ) : (
                  <span className="text-[11px] tracking-[0.14em] uppercase l-t-muted shrink-0">{m.role}</span>
                )}
              </div>
            ))}
          </div>
        </div>

        {error && <p className="text-[13px] l-t-rust mt-3">{error}</p>}

        <div className="flex items-center gap-3 mt-5">
          <button onClick={onClose} className="l-btn">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
