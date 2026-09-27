"use client";

/** The few shapes every signed-in page is built from. Styling lives in
 * globals.css (.p-*) so a page reads as structure, not as utility strings. */
import type { ReactNode } from "react";

export type Tone = "ok" | "warn" | "bad" | "info" | "accent" | "neutral";

export function PageHeader({ title, description, actions }: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[18px] font-semibold leading-tight">{title}</h1>
        {description && <p className="mt-1 max-w-2xl p-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className = "", flush = false }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  /** No body padding — for tables and lists that run edge to edge. */
  flush?: boolean;
}) {
  return (
    <section className={`p-card ${className}`}>
      {(title || actions) && (
        <div className="p-card-h">
          <div className="p-card-t">{title}</div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      {/* Tables scroll inside their card on a phone rather than widening the page. */}
      <div className={flush ? "overflow-x-auto" : "p-card-b"}>{children}</div>
    </section>
  );
}

export function Badge({ tone = "neutral", children, dot = false }: {
  tone?: Tone;
  children: ReactNode;
  dot?: boolean;
}) {
  return (
    <span className="p-badge" data-tone={tone === "neutral" ? undefined : tone}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="px-4 py-8 text-center">
      <p className="font-medium">{title}</p>
      {children && <p className="mx-auto mt-1 max-w-md p-muted">{children}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: Tone }) {
  return (
    <div className="p-card px-4 py-3">
      <div className="p-eyebrow">{label}</div>
      <div className="mt-1 text-[20px] font-semibold leading-none" style={tone === "bad" ? { color: "var(--p-bad)" } : undefined}>
        {value}
      </div>
      {hint && <div className="mt-1 p-hint">{hint}</div>}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="p-label">{label}</span>
      {children}
      {hint && <span className="mt-1 block p-hint">{hint}</span>}
    </label>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="mb-4 rounded-lg px-3 py-2 text-[12.5px]" style={{ background: "var(--p-bad-bg)", color: "var(--p-bad)" }}>
      {children}
    </div>
  );
}

// ── agent-job status, shared by Home and Tickets ─────────────────────────

export const JOB_STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Confirm draft", tone: "accent" },
  planning: { label: "Planning", tone: "info" },
  awaiting_approval: { label: "Needs approval", tone: "warn" },
  fixing: { label: "Fixing", tone: "info" },
  pr_open: { label: "PR open", tone: "ok" },
  escalated: { label: "Escalated", tone: "bad" },
  rejected: { label: "Rejected", tone: "neutral" },
  failed: { label: "Failed", tone: "bad" },
};

export function JobBadge({ status }: { status: string }) {
  const s = JOB_STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <Badge tone={s.tone} dot={status === "planning" || status === "fixing"}>{s.label}</Badge>;
}

export const SOURCE_LABEL: Record<string, string> = {
  github: "GitHub",
  linear: "Linear",
  jira: "Jira",
  manual: "Manual",
  meeting: "From a call",
};

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  // The API returns naive UTC; without the Z every time is off by the offset.
  const t = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
