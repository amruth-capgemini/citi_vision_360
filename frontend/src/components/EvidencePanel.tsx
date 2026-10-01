import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRightIcon } from "lucide-react";
import type { ChatResponse, QueryView, ToolCallView } from "../api";
import { flagText, noteText } from "../api";
import { formatQuery } from "../format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Item, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import { AgentBadge, EmptyNote, RUN_STATUS, Section, ms } from "./common";

interface Props {
  result: ChatResponse;
  focusFact: string | null;
}

type Filter = "all" | "sql" | "cypher" | "kept";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "sql", label: "SQL" },
  { value: "cypher", label: "Cypher" },
  { value: "kept", label: "Kept" },
];

/**
 * Evidence: which agents ran, every SQL and Cypher query they attempted (with the tables or
 * graph it ran on and what it returned), every certified tool call, and each fact card's source.
 */
export function EvidencePanel({ result, focusFact }: Props) {
  const { agents, tool_calls: calls, queries } = result.evidence_view;
  const [filter, setFilter] = useState<Filter>("all");
  const [agent, setAgent] = useState<string>("all");
  const factRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    if (focusFact) factRefs.current[focusFact]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusFact]);

  const shown = queries.filter(
    (q) => (agent === "all" || q.agent === agent) && (filter === "all" || (filter === "kept" ? q.kept : q.language === filter)),
  );
  const tables = useMemo(() => {
    const all = new Set<string>();
    queries.forEach((q) => (q.language === "sql" ? q.datasets.forEach((d) => all.add(d)) : all.add(`${q.graph ?? "business"} graph`)));
    calls.forEach((c) => c.sources.forEach((s) => all.add(s)));
    return [...all].sort();
  }, [queries, calls]);
  const evidence = useMemo(() => new Map(result.evidence.map((e) => [e.evidence_id, e.source])), [result.evidence]);
  const queryAgents = [...new Set(queries.map((q) => q.agent))];
  const agentItems = [{ label: "All agents", value: "all" }, ...queryAgents.map((a) => ({ label: a, value: a }))];
  const selected = new Set(result.selected_fact_ids ?? []);

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(7.5rem,1fr))] gap-2">
        <Stat value={agents.filter((a) => a.used).length} label="agents used" />
        <Stat value={calls.length} label="certified tool calls" />
        <Stat value={queries.length} label={`queries (${queries.filter((q) => q.kept).length} kept)`} />
        <Stat value={tables.length} label="tables & graphs" />
        <Stat value={result.facts.length} label="fact cards" />
      </div>

      <Section title="Flags & limitations" count={result.flags.length + result.limitations.length}>
        {result.flags.length === 0 && result.limitations.length === 0 && <EmptyNote>None reported.</EmptyNote>}
        <ul className="flex flex-col gap-1.5">
          {result.flags.map((f, i) => (
            <li key={`f${i}`} className="flex items-start gap-2">
              <Badge variant="destructive">flag</Badge>
              <span>
                {flagText(f)} <span className="text-muted-foreground">({f.tool_id})</span>
              </span>
            </li>
          ))}
          {result.limitations.map((n, i) => (
            <li key={`l${i}`} className="flex items-start gap-2">
              <Badge variant="warning">{typeof n === "string" ? "note" : n.code ?? "note"}</Badge>
              <span>{noteText(n)}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Agents" count={agents.length}>
        {agents.length === 0 ? (
          <EmptyNote>No agent ran for this question.</EmptyNote>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Certified tools</TableHead>
                <TableHead>Queries run / kept</TableHead>
                <TableHead>Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {agents.map((a) => (
                <TableRow key={a.agent} className={cn(!a.used && "opacity-55")}>
                  <TableCell>
                    <AgentBadge agent={a.agent} />
                  </TableCell>
                  <TableCell>{a.used ? a.status : `${a.status} · not used`}</TableCell>
                  <TableCell className="font-mono text-xs">{a.tools.join(", ") || "—"}</TableCell>
                  <TableCell>{a.role === "supervisor" ? "—" : `${a.queries_run} / ${a.queries_kept}`}</TableCell>
                  <TableCell className="tabular-nums">{ms(a.duration_ms)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      <Section
        title="Queries run (SQL & Cypher)"
        count={queries.length}
        right={
          queries.length > 0 && (
            <div className="flex flex-wrap items-center justify-end gap-2">
              <ToggleGroup variant="outline" size="sm" spacing={0} value={[filter]} onValueChange={(v) => v[0] && setFilter(v[0] as Filter)}>
                {FILTERS.map((f) => (
                  <ToggleGroupItem key={f.value} value={f.value}>
                    {f.label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
              {queryAgents.length > 1 && (
                <Select items={agentItems} value={agent} onValueChange={(v) => setAgent(v ?? "all")}>
                  <SelectTrigger size="sm" aria-label="Filter by agent">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {agentItems.map((a) => (
                        <SelectItem key={a.value} value={a.value}>
                          {a.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              )}
            </div>
          )
        }
      >
        {queries.length === 0 ? (
          <EmptyNote>No dynamic queries ran; the answer comes from certified tools only.</EmptyNote>
        ) : shown.length === 0 ? (
          <EmptyNote>No queries match this filter.</EmptyNote>
        ) : (
          shown.map((q, i) => <QueryCard key={`${q.agent}-${q.round}-${q.query_id}-${i}`} q={q} />)
        )}
      </Section>

      <Section title="Certified tool calls" count={calls.length}>
        {calls.length === 0 ? <EmptyNote>No certified tool was called.</EmptyNote> : calls.map((c) => <ToolCard key={c.tool_id} c={c} />)}
      </Section>

      <Section title="Tables & graphs used" count={result.sources_used.length}>
        {result.sources_used.length === 0 ? (
          <EmptyNote>No source was read.</EmptyNote>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Source</TableHead>
                <TableHead>Read via</TableHead>
                <TableHead>By agent</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.sources_used.map((s) => (
                <TableRow key={s.source}>
                  <TableCell className="font-mono text-xs">{s.source}</TableCell>
                  <TableCell className="font-mono text-xs">{s.via.join(", ")}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {s.specialists.map((a) => (
                        <AgentBadge key={a} agent={a} />
                      ))}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      <Section title="Fact cards" count={result.facts.length}>
        {result.facts.length === 0 ? (
          <EmptyNote>No fact cards.</EmptyNote>
        ) : (
          <ItemGroup className="gap-2">
            {result.facts.map((f) => (
              <Item
                key={f.fact_id}
                ref={(el: HTMLDivElement | null) => {
                  factRefs.current[f.fact_id] = el;
                }}
                variant={selected.has(f.fact_id) ? "outline" : "muted"}
                size="sm"
                className={cn("scroll-my-4", focusFact === f.fact_id && "border-ring ring-3 ring-ring/50")}
              >
                <ItemContent>
                  <ItemTitle className="flex-wrap">
                    <span className="font-mono text-xs font-bold">{f.fact_id}</span>
                    <Badge variant="secondary">{f.topic}</Badge>
                    <span className="text-xs font-normal text-muted-foreground">from {f.tool_id}</span>
                  </ItemTitle>
                  <ItemDescription className="line-clamp-none text-foreground">{f.text}</ItemDescription>
                  {f.evidence_ids.length > 0 && (
                    <Disclosure label={`Source: ${f.sources.join(" · ") || f.evidence_ids.join(", ")}`}>
                      {f.evidence_ids.map((id) => (
                        <SourceDetail key={id} id={id} source={evidence.get(id)} />
                      ))}
                    </Disclosure>
                  )}
                </ItemContent>
              </Item>
            ))}
          </ItemGroup>
        )}
      </Section>
    </div>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-xl tabular-nums">{value}</CardTitle>
      </CardHeader>
    </Card>
  );
}

/** A collapsed block behind a small chevron trigger. */
function Disclosure({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Collapsible className="flex flex-col gap-2">
      <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="group/disclosure w-fit max-w-full justify-start" />}>
        <ChevronRightIcon data-icon="inline-start" className="transition-transform group-data-panel-open/disclosure:rotate-90" />
        <span className="truncate">{label}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="flex flex-col gap-2">{children}</CollapsibleContent>
    </Collapsible>
  );
}

function CodeBlock({ children, className }: { children: string; className?: string }) {
  return <pre className={cn("overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap", className)}>{children}</pre>;
}

function WhereRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="min-w-16 text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function Mono({ children }: { children: React.ReactNode }) {
  return (
    <Badge variant="outline" className="font-mono">
      {children}
    </Badge>
  );
}

function QueryCard({ q }: { q: QueryView }) {
  const [view, setView] = useState<"formatted" | "raw">("formatted");
  const where = q.language === "sql" ? q.datasets : [`${q.graph ?? "business"} graph`, ...(q.labels ?? []).map((l) => `:${l}`)];
  const rows = q.row_count !== null && q.row_count !== undefined ? `${q.row_count} row${q.row_count === 1 ? "" : "s"}${q.truncated ? " (truncated)" : ""} · ` : "";
  return (
    <Card size="sm" className="bg-background">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-1.5">
          <AgentBadge agent={q.agent} />
          <Badge variant="secondary">{q.language.toUpperCase()}</Badge>
          {q.query_id && <span className="font-mono text-xs font-bold">{q.query_id}</span>}
          {q.round > 0 && <Badge variant="outline">round {q.round + 1}</Badge>}
          <Badge variant={RUN_STATUS[q.status] ?? "secondary"}>{q.status}</Badge>
          {q.kept && <Badge variant="success">kept as evidence</Badge>}
        </CardTitle>
        {q.purpose && <CardDescription>{q.purpose.replace("_", " ")}</CardDescription>}
        <CardAction className="text-xs text-muted-foreground tabular-nums">
          {rows}
          {ms(q.duration_ms)}
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <WhereRow label={q.language === "sql" ? "Tables" : "Graph"}>
          {where.length ? where.map((w) => <Mono key={w}>{w}</Mono>) : <span className="text-muted-foreground">not reached (the guard stopped it)</span>}
        </WhereRow>
        {q.thought && <p className="text-xs text-muted-foreground italic">“{q.thought}”</p>}
        <div className="flex flex-col gap-1.5">
          <ToggleGroup size="sm" spacing={0} variant="outline" value={[view]} onValueChange={(v) => v[0] && setView(v[0] as "formatted" | "raw")} className="self-end">
            <ToggleGroupItem value="formatted">Formatted</ToggleGroupItem>
            <ToggleGroupItem value="raw">As executed</ToggleGroupItem>
          </ToggleGroup>
          <CodeBlock>{view === "raw" ? q.query : formatQuery(q.query, q.language)}</CodeBlock>
        </div>
        {q.message && <p className={cn("text-xs", q.status === "ok" ? "text-muted-foreground" : "text-destructive")}>{q.message}</p>}
        {q.kept && q.columns && q.rows && q.rows.length > 0 && (
          <Disclosure label={`Result rows (${q.rows.length})${q.fact_ids?.length ? ` · cited as ${q.fact_ids.join(", ")}` : ""}`}>
            <Table className="font-mono text-xs">
              <TableHeader>
                <TableRow>
                  {q.columns.map((c) => (
                    <TableHead key={c}>{c}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {q.rows.map((row, i) => (
                  <TableRow key={i}>
                    {row.map((v, j) => (
                      <TableCell key={j}>{cell(v)}</TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {q.records && q.records.length > 0 && <p className="text-xs text-muted-foreground">Source records: {q.records.join(", ")}</p>}
          </Disclosure>
        )}
      </CardContent>
    </Card>
  );
}

function ToolCard({ c }: { c: ToolCallView }) {
  const args = Object.entries(c.arguments).filter(([, v]) => v !== null && v !== undefined);
  return (
    <Card size="sm" className="bg-background">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-1.5">
          <AgentBadge agent={c.agent} />
          <Badge variant="success">certified</Badge>
          <code className="font-mono text-sm">{c.tool}</code>
          <span className="font-mono text-xs font-bold text-muted-foreground">{c.tool_id}</span>
        </CardTitle>
        <CardAction className="text-xs text-muted-foreground tabular-nums">
          {c.evidence_count} evidence · {ms(c.duration_ms)}
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <WhereRow label="Arguments">
          {args.length ? (
            args.map(([k, v]) => <Mono key={k}>{`${k}=${typeof v === "string" ? v : JSON.stringify(v)}`}</Mono>)
          ) : (
            <span className="text-muted-foreground">defaults</span>
          )}
        </WhereRow>
        {groupSources(c.sources).map(([kind, names]) => (
          <WhereRow key={kind} label={kind}>
            {names.map((s) => (
              <Mono key={s}>{s}</Mono>
            ))}
          </WhereRow>
        ))}
        {c.fact_ids.length > 0 && <p className="text-xs text-muted-foreground">Fact cards: {c.fact_ids.join(", ")}</p>}
      </CardContent>
    </Card>
  );
}

/** Tables, graph relationships and documents a certified tool read, each kind on its own line. */
function groupSources(sources: string[]): [string, string[]][] {
  const groups: Record<string, string[]> = { Tables: [], "Graph rels": [], Documents: [], Other: [] };
  for (const s of sources) {
    if (s.startsWith("graph :")) groups["Graph rels"].push(s.slice(6));
    else if (s.startsWith("document ")) groups.Documents.push(s.slice(9));
    else if (s.startsWith("graph ")) groups.Other.push(s);
    else groups.Tables.push(s);
  }
  return Object.entries(groups).filter(([, names]) => names.length > 0);
}

function SourceDetail({ id, source }: { id: string; source?: Record<string, unknown> }) {
  if (!source) return null;
  const { rows, columns, ...rest } = source as Record<string, unknown>;
  void rows;
  void columns;
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-xs font-bold">{id}</span>
      <CodeBlock className="text-[11px]">{JSON.stringify(rest, null, 2)}</CodeBlock>
    </div>
  );
}

function cell(v: unknown) {
  if (v === null || v === undefined) return <span className="text-muted-foreground">null</span>;
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}
