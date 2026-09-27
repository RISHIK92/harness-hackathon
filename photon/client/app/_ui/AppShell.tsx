"use client";

/** The frame every signed-in page sits in: workspace, navigation, account.
 *
 * Before this, each page was an island — the dashboard had its own header,
 * /agent and /whisper had none, and the workspace switcher (with members and
 * invites hidden inside it) existed only on the dashboard. Now there is one
 * place to see where you are, switch workspace, and reach everything, and
 * the counts on the nav say where your attention is needed.
 *
 * The live call (/call) is deliberately NOT inside this: a call is a
 * full-screen room, and a sidebar next to video is noise.
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import AuthGuard from "../AuthGuard";
import {
  createWorkspace,
  getMe,
  getWorkspaceId,
  joinWorkspace,
  listAgentJobs,
  listWorkspaces,
  logout,
  myEscalations,
  setWorkspaceId,
  type Me,
  type Workspace,
} from "@/lib/api";

type Ctx = { workspace: Workspace | null; workspaces: Workspace[]; me: Me | null; isOwner: boolean; reload: () => void };
const WorkspaceCtx = createContext<Ctx>({ workspace: null, workspaces: [], me: null, isOwner: false, reload: () => {} });
export const useWorkspace = () => useContext(WorkspaceCtx);

const ICON: Record<string, ReactNode> = {
  home: <path d="M3 10.5 10 4l7 6.5V17a1 1 0 0 1-1 1h-4v-5H8v5H4a1 1 0 0 1-1-1z" />,
  tickets: <path d="M4 5h12v3a2 2 0 0 0 0 4v3H4v-3a2 2 0 0 0 0-4zM8 5v10" />,
  calls: <path d="M4 6h8v8H4zM12 9l4-2.5v7L12 11" />,
  whisper: <path d="M4 5h12v8H9l-4 3v-3H4z" />,
  knowledge: <path d="M4 4h5a2 2 0 0 1 2 2v10a2 2 0 0 0-2-2H4zM16 4h-5a2 2 0 0 0-2 2v10a2 2 0 0 1 2-2h5z" />,
  me: <path d="M10 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM4 17c0-3 2.7-5 6-5s6 2 6 5" />,
  admin: <path d="M10 3l6 2.5v4.2c0 3.6-2.6 6.3-6 7.3-3.4-1-6-3.7-6-7.3V5.5z" />,
};

function Icon({ name }: { name: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {ICON[name]}
    </svg>
  );
}

function WorkspaceMenu({ ctx }: { ctx: Ctx }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"list" | "new" | "join">("list");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"team" | "individual">("team");
  const [msg, setMsg] = useState<string | null>(null);

  const switchTo = (id: string) => {
    setWorkspaceId(id);
    window.location.reload();
  };
  const create = async () => {
    try {
      const w = await createWorkspace(name.trim(), kind);
      switchTo(w.id);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };
  const join = async () => {
    try {
      const r = await joinWorkspace(name.trim());
      if (r.status === "already_member") switchTo(r.workspace.id);
      else setMsg(`Asked to join ${r.workspace.name} — an owner has to approve it.`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => { setOpen((v) => !v); setMode("list"); setMsg(null); }}
        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-[rgba(28,25,23,.05)]"
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold text-white"
              style={{ background: "var(--l-rust)" }}>
          {(ctx.workspace?.name || "·").slice(0, 1).toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{ctx.workspace?.name ?? "…"}</span>
          <span className="block text-[11px] p-muted">
            {ctx.workspace ? `${ctx.workspace.role} · ${ctx.workspace.kind === "team" ? "team" : "personal"}` : ""}
          </span>
        </span>
        <span className="p-muted text-[10px]">▾</span>
      </button>
      {open && (
        <>
          <button className="fixed inset-0 z-10 cursor-default" aria-label="Close" onClick={() => setOpen(false)} />
          <div className="p-card absolute left-0 right-0 top-11 z-20 p-1.5 shadow-lg">
            {mode === "list" && (
              <>
                {ctx.workspaces.map((w) => (
                  <button key={w.id} onClick={() => switchTo(w.id)}
                          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-[rgba(28,25,23,.05)]">
                    <span className="h-1.5 w-1.5 rounded-full"
                          style={{ background: w.id === ctx.workspace?.id ? "var(--l-rust)" : "var(--l-rule)" }} />
                    <span className="truncate">{w.name}</span>
                    {w.is_personal && <span className="ml-auto text-[10px] p-muted">personal</span>}
                  </button>
                ))}
                <div className="my-1 h-px" style={{ background: "var(--l-rule)" }} />
                <button onClick={() => { setMode("new"); setName(""); }} className="w-full rounded-md px-2 py-1.5 text-left p-muted hover:bg-[rgba(28,25,23,.05)]">
                  + New workspace
                </button>
                <button onClick={() => { setMode("join"); setName(""); }} className="w-full rounded-md px-2 py-1.5 text-left p-muted hover:bg-[rgba(28,25,23,.05)]">
                  Join with an invite code
                </button>
              </>
            )}
            {mode !== "list" && (
              <div className="space-y-2 p-1.5">
                <input autoFocus className="p-input" value={name} onChange={(e) => setName(e.target.value)}
                       placeholder={mode === "new" ? "Workspace name" : "Invite code"}
                       onKeyDown={(e) => e.key === "Enter" && name.trim() && void (mode === "new" ? create() : join())} />
                {mode === "new" && (
                  <div className="flex gap-1">
                    {(["team", "individual"] as const).map((k) => (
                      <button key={k} onClick={() => setKind(k)}
                              className={k === kind ? "p-btn flex-1 justify-center" : "p-btn-quiet flex-1 justify-center"}>
                        {k === "team" ? "Team" : "Just me"}
                      </button>
                    ))}
                  </div>
                )}
                {msg && <p className="p-hint">{msg}</p>}
                <div className="flex justify-between">
                  <button onClick={() => setMode("list")} className="p-hint">Back</button>
                  <button disabled={!name.trim()} onClick={() => void (mode === "new" ? create() : join())} className="p-btn">
                    {mode === "new" ? "Create" : "Join"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function AlertsToggle() {
  const [perm, setPerm] = useState<string>(() => {
    try {
      return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
    } catch {
      return "unsupported";
    }
  });
  if (perm === "granted" || perm === "unsupported") {
    return <span className="p-hint">{perm === "granted" ? "Alerts on" : ""}</span>;
  }
  return (
    <button className="p-hint text-left underline" title="Your agent asks you mid-meeting with a 60-second window"
            onClick={() => void Notification.requestPermission().then(setPerm)}>
      {perm === "denied" ? "Alerts blocked in browser settings" : "Turn on alerts for escalations"}
    </button>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [me, setMe] = useState<Me | null>(null);
  const [counts, setCounts] = useState({ tickets: 0, asked: 0 });
  const [mobileNav, setMobileNav] = useState(false);

  const load = useCallback(() => {
    listWorkspaces().then(setWorkspaces).catch(() => setWorkspaces([]));
    getMe().then(setMe).catch(() => setMe(null));
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const tick = () =>
      Promise.all([listAgentJobs().catch(() => []), myEscalations().catch(() => [])]).then(([jobs, esc]) =>
        setCounts({
          tickets: jobs.filter((j) => j.status === "awaiting_approval" || j.status === "draft" || j.status === "escalated").length,
          asked: esc.filter((e) => e.status === "open" || e.status === "answered_late" || e.status === "expired").length,
        }),
      );
    void tick();
    const t = setInterval(tick, 15000);
    return () => clearInterval(t);
  }, []);

  const current = getWorkspaceId();
  const workspace = workspaces.find((w) => w.id === current) ?? workspaces.find((w) => w.is_personal) ?? workspaces[0] ?? null;
  const isOwner = workspace?.role?.toLowerCase() === "owner";
  const ctx: Ctx = { workspace, workspaces, me, isOwner, reload: load };

  const nav: { href: string; label: string; icon: string; count?: number; show?: boolean }[] = [
    { href: "/home", label: "Home", icon: "home" },
    { href: "/agent", label: "Tickets", icon: "tickets", count: counts.tickets },
    { href: "/call", label: "Calls", icon: "calls" },
    { href: "/whisper", label: "Whisper", icon: "whisper" },
    { href: "/dashboard", label: "Knowledge", icon: "knowledge" },
    { href: "/me", label: "My agent", icon: "me", count: counts.asked },
    { href: "/admin", label: "Admin", icon: "admin", show: isOwner && !workspace?.is_personal },
  ];

  const side = (
    <div className="flex h-full flex-col gap-4 p-3">
      <Link href="/home" className="px-2 pt-1 text-[22px] italic leading-none"
            style={{ fontFamily: "var(--font-display)", color: "var(--l-ink)" }}>
        photon
      </Link>
      <WorkspaceMenu ctx={ctx} />
      <nav className="flex flex-col gap-0.5">
        {nav.filter((n) => n.show !== false).map((n) => (
          <Link key={n.href} href={n.href} className="p-nav" onClick={() => setMobileNav(false)}
                aria-current={pathname === n.href || pathname?.startsWith(`${n.href}/`) ? "page" : undefined}>
            <Icon name={n.icon} />
            {n.label}
            {n.count ? <span className="p-nav-count">{n.count}</span> : null}
          </Link>
        ))}
      </nav>
      <div className="mt-auto space-y-2 border-t px-2 pt-3" style={{ borderColor: "var(--l-rule)" }}>
        <AlertsToggle />
        <div className="truncate text-[12px] p-muted" title={me?.email}>{me?.email}</div>
        <button className="p-hint underline" onClick={() => { logout(); router.replace("/login"); }}>Sign out</button>
      </div>
    </div>
  );

  return (
    <WorkspaceCtx.Provider value={ctx}>
      <div className="p-app flex min-h-screen">
        <aside className="p-side sticky top-0 hidden h-screen w-[220px] shrink-0 md:block">{side}</aside>
        {mobileNav && (
          <div className="fixed inset-0 z-40 md:hidden">
            <button className="absolute inset-0 bg-black/20" aria-label="Close menu" onClick={() => setMobileNav(false)} />
            <aside className="p-side relative h-full w-[240px]">{side}</aside>
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3 border-b px-4 py-2 md:hidden" style={{ borderColor: "var(--l-rule)" }}>
            <button className="p-btn-quiet" onClick={() => setMobileNav(true)} aria-label="Menu">☰</button>
            <span className="font-medium">{workspace?.name}</span>
          </div>
          <main className="mx-auto max-w-[1120px] px-4 py-6 md:px-8">{children}</main>
        </div>
      </div>
    </WorkspaceCtx.Provider>
  );
}

/** Wrap a signed-in page: auth check, then the frame. */
export default function AppShell({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <Shell>{children}</Shell>
    </AuthGuard>
  );
}
