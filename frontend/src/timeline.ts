import type { TraceEntry } from "./api";

/** One row of the live agent timeline, built from SSE node and step events (or a finished trace). */
export interface TimelineItem {
  key: string;
  node: string;
  agent?: string;
  status: string;
  duration_ms?: number;
  entry?: TraceEntry;
  steps: TraceEntry[];
  live: boolean;
}

let counter = 0;
const nextKey = () => `t${++counter}`;

export function addNode(items: TimelineItem[], entry: TraceEntry): TimelineItem[] {
  if (entry.node === "explore") {
    // Steps arrive first; close the explorer group they opened for this agent.
    const index = items.findIndex((i) => i.node === "explore" && i.live && i.agent === entry.agent);
    if (index >= 0) {
      const copy = items.slice();
      copy[index] = { ...copy[index], live: false, status: entry.status, duration_ms: entry.duration_ms, entry };
      return copy;
    }
    return [...items, { key: nextKey(), node: "explore", agent: entry.agent, status: entry.status, duration_ms: entry.duration_ms,
                        entry, steps: entry.steps ?? [], live: false }];
  }
  return [...items, { key: nextKey(), node: entry.node, agent: entry.agent, status: entry.status, duration_ms: entry.duration_ms,
                      entry, steps: [], live: false }];
}

export function addStep(items: TimelineItem[], step: TraceEntry): TimelineItem[] {
  const index = items.findIndex((i) => i.node === "explore" && i.live && i.agent === step.agent);
  if (index < 0) {
    return [...items, { key: nextKey(), node: "explore", agent: step.agent, status: "running", steps: [step], live: true }];
  }
  const copy = items.slice();
  copy[index] = { ...copy[index], steps: [...copy[index].steps, step] };
  return copy;
}

export function fromTrace(trace: TraceEntry[]): TimelineItem[] {
  return trace.reduce(addNode, [] as TimelineItem[]);
}

const LABELS: Record<string, string> = {
  guard: "Input guard",
  route: "Supervisor routes the question",
  resolve: "Resolve vendors",
  plan: "Plan tool calls",
  validate: "Validate plans",
  "execute.tool": "Certified tool",
  execute: "Certified tools done",
  explore: "Source explorer",
  verify: "Verify coverage",
  ground: "Ground facts & evidence",
  synthesize: "Write the answer",
  stop: "Stopped",
};

export function label(item: TimelineItem): string {
  return LABELS[item.node] ?? item.node;
}

/** A short, human detail line for a trace entry (never the raw JSON dump). */
export function detail(item: TimelineItem): string {
  const e = item.entry ?? ({} as TraceEntry);
  switch (item.node) {
    case "route":
      return `${(e.specialists as string[] | undefined)?.join(", ") ?? ""} · focus ${e.focus ?? "-"} · ${e.route_status ?? ""}`;
    case "resolve": {
      const ids = (e.vendor_ids as string[] | undefined) ?? [];
      const only = e.explore_only as string[] | undefined;
      return `${ids.length ? ids.join(", ") : "no vendor (portfolio)"}${only?.length ? ` · explore only: ${only.join(", ")}` : ""}`;
    }
    case "plan":
      return e.status === "declined" || e.status === "rejected" ? String(e.status) : ((e.tools as string[] | undefined) ?? []).join(", ");
    case "validate":
      return `${e.tool_calls ?? 0} tool call(s)${(e.declined as string[] | undefined)?.length ? ` · declined ${(e.declined as string[]).join(", ")}` : ""}`;
    case "execute.tool":
      return `${e.action} ${JSON.stringify(compact(e.arguments as Record<string, unknown> | undefined))} · ${e.evidence ?? 0} evidence`;
    case "explore": {
      const queries = item.steps.filter((s) => s.action === "run_sql" || s.action === "run_cypher").length;
      return item.live ? `${queries} quer${queries === 1 ? "y" : "ies"} so far…` : `${e.queries ?? queries} run · kept ${((e.kept as string[]) ?? []).join(", ") || "none"}`;
    }
    case "verify": {
      const gaps = Object.entries((e.gaps as Record<string, string[]>) ?? {});
      return gaps.length ? `gaps: ${gaps.map(([a, g]) => `${a} (${g.join(", ")})`).join("; ")}` : "all concepts covered";
    }
    case "ground":
      return `${e.tool_results ?? 0} results · ${e.facts ?? 0} facts · ${((e.sources as string[]) ?? []).length} sources`;
    case "synthesize":
      return e.fallback ? `fallback to source cards after ${e.attempts} attempt(s)` : `${e.selected ?? 0} facts cited · ${e.attempts} attempt(s)`;
    case "stop":
      return `${e.status} (${e.code})`;
    default:
      return "";
  }
}

function compact(args: Record<string, unknown> | undefined) {
  return Object.fromEntries(Object.entries(args ?? {}).filter(([, v]) => v !== null && v !== undefined));
}
