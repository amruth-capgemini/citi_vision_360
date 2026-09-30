import type { ReactNode } from "react";
import type { Status } from "../api";

const STATUS_LABEL: Record<Status, string> = {
  answered: "Answered",
  clarification: "Needs clarification",
  not_found: "Not found",
  unsupported: "Unsupported",
  unavailable: "Unavailable",
};

export function StatusBadge({ status }: { status: Status | "running" }) {
  const label = status === "running" ? "Working…" : STATUS_LABEL[status] ?? status;
  return <span className={`badge status-${status}`}>{label}</span>;
}

export function Pill({ children, tone = "neutral", title }: { children: ReactNode; tone?: string; title?: string }) {
  return (
    <span className={`pill tone-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Section({ title, count, children, right }: { title: string; count?: number | string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="section">
      <header className="section-head">
        <h3>
          {title}
          {count !== undefined && <span className="count">{count}</span>}
        </h3>
        {right}
      </header>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function ms(value?: number | null) {
  if (value === undefined || value === null) return "";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`;
}

export const AGENT_TONE: Record<string, string> = {
  supervisor: "slate",
  vendor360: "blue",
  renewal: "violet",
  risk_dependency: "red",
  rationalization: "teal",
  spend_forecast: "amber",
  what_if: "green",
};

export function AgentPill({ agent }: { agent?: string }) {
  if (!agent) return null;
  return <Pill tone={AGENT_TONE[agent] ?? "neutral"}>{agent}</Pill>;
}
