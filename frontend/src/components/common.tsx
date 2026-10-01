import type { ReactNode } from "react";
import type { Status } from "../api";
import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader } from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

type BadgeVariant = "default" | "secondary" | "destructive" | "success" | "warning" | "outline" | "ghost";

const STATUS: Record<Status, { label: string; variant: BadgeVariant }> = {
  answered: { label: "Answered", variant: "success" },
  clarification: { label: "Needs clarification", variant: "warning" },
  not_found: { label: "Not found", variant: "secondary" },
  unsupported: { label: "Unsupported", variant: "destructive" },
  unavailable: { label: "Unavailable", variant: "destructive" },
};

export function StatusBadge({ status }: { status: Status | "running" }) {
  if (status === "running") {
    return (
      <Badge variant="outline">
        <Spinner data-icon="inline-start" />
        Working…
      </Badge>
    );
  }
  const { label, variant } = STATUS[status] ?? { label: status, variant: "secondary" };
  return <Badge variant={variant}>{label}</Badge>;
}

/** Badge variant for a query or step status. */
export const RUN_STATUS: Record<string, BadgeVariant> = {
  ok: "success",
  empty: "secondary",
  rejected: "destructive",
  error: "destructive",
  refused: "warning",
  stop: "destructive",
};

/** A titled panel section: title, optional count and header controls. */
export function Section({
  title,
  description,
  count,
  children,
  right,
}: {
  title: string;
  description?: ReactNode;
  count?: number | string;
  children: ReactNode;
  right?: ReactNode;
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {title}
          {count !== undefined && <Badge variant="secondary">{count}</Badge>}
        </CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
        {right && <CardAction>{right}</CardAction>}
      </CardHeader>
      <CardContent className="flex flex-col gap-2">{children}</CardContent>
    </Card>
  );
}

export function EmptyNote({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <Empty className={cn("p-4", className)}>
      <EmptyHeader>
        <EmptyDescription>{children}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  );
}

export function ms(value?: number | null) {
  if (value === undefined || value === null) return "";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`;
}

// Literal class names so Tailwind generates them.
const AGENT_DOT: Record<string, string> = {
  supervisor: "bg-muted-foreground",
  vendor360: "bg-chart-1",
  renewal: "bg-chart-2",
  risk_dependency: "bg-chart-3",
  rationalization: "bg-chart-4",
  spend_forecast: "bg-chart-5",
  what_if: "bg-chart-6",
};

export function AgentBadge({ agent }: { agent?: string }) {
  if (!agent) return null;
  return (
    <Badge variant="outline">
      <span aria-hidden className={cn("size-1.5 rounded-full", AGENT_DOT[agent] ?? "bg-muted-foreground")} />
      {agent}
    </Badge>
  );
}
