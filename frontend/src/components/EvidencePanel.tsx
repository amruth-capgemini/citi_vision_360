import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatResponse, QueryView, ToolCallView } from "../api";
import { flagText, noteText } from "../api";
import { AgentPill, Empty, Pill, Section, ms } from "./common";
import { formatQuery } from "../format";

interface Props {
  result: ChatResponse;
  focusFact: string | null;
}

type Filter = "all" | "sql" | "cypher" | "kept";

/**
 * Evidence: which agents ran, every SQL and Cypher query they attempted (with the tables or
 * graph it ran on and what it returned), every certified tool call, and each fact card's source.
 */
export function EvidencePanel({ result, focusFact }: Props) {
  const { agents, tool_calls: calls, queries } = result.evidence_view;
  const [filter, setFilter] = useState<Filter>("all");
  const [agent, setAgent] = useState<string>("all");
  const factRefs = useRef<Record<string, HTMLLIElement | null>>({});

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

  return (
    <div className="evidence">
      <div className="stats">
        <Stat value={agents.filter((a) => a.used).length} label="agents used" />
        <Stat value={calls.length} label="certified tool calls" />
        <Stat value={queries.length} label={`queries (${queries.filter((q) => q.kept).length} kept)`} />
        <Stat value={tables.length} label="tables & graphs" />
        <Stat value={result.facts.length} label="fact cards" />
      </div>

      <Section title="Flags & limitations" count={result.flags.length + result.limitations.length}>
        {result.flags.length === 0 && result.limitations.length === 0 && <Empty>None reported.</Empty>}
        <ul className="notes">
          {result.flags.map((f, i) => (
            <li key={`f${i}`} className="note flag">
              <Pill tone="red">flag</Pill> {flagText(f)} <span className="muted">({f.tool_id})</span>
            </li>
          ))}
          {result.limitations.map((n, i) => (
            <li key={`l${i}`} className="note">
              <Pill tone="amber">{typeof n === "string" ? "note" : n.code ?? "note"}</Pill> {noteText(n)}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Agents" count={agents.length}>
        {agents.length === 0 ? (
          <Empty>No agent ran for this question.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Status</th>
                  <th>Certified tools</th>
                  <th>Queries run / kept</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.agent} className={a.used ? "" : "dim"}>
                    <td>
                      <AgentPill agent={a.agent} />
                    </td>
                    <td>{a.used ? a.status : `${a.status} · not used`}</td>
                    <td className="mono">{a.tools.join(", ") || "—"}</td>
                    <td>{a.role === "supervisor" ? "—" : `${a.queries_run} / ${a.queries_kept}`}</td>
                    <td>{ms(a.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section
        title="Queries run (SQL & Cypher)"
        count={queries.length}
        right={
          queries.length > 0 && (
            <div className="filters">
              {(["all", "sql", "cypher", "kept"] as Filter[]).map((f) => (
                <button key={f} className={`chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>
                  {f === "all" ? "All" : f === "kept" ? "Kept" : f.toUpperCase()}
                </button>
              ))}
              {queryAgents.length > 1 && (
                <select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Filter by agent">
                  <option value="all">All agents</option>
                  {queryAgents.map((a) => (
                    <option key={a}>{a}</option>
                  ))}
                </select>
              )}
            </div>
          )
        }
      >
        {queries.length === 0 ? (
          <Empty>No dynamic queries ran; the answer comes from certified tools only.</Empty>
        ) : shown.length === 0 ? (
          <Empty>No queries match this filter.</Empty>
        ) : (
          shown.map((q, i) => <QueryCard key={`${q.agent}-${q.round}-${q.query_id}-${i}`} q={q} />)
        )}
      </Section>

      <Section title="Certified tool calls" count={calls.length}>
        {calls.length === 0 ? <Empty>No certified tool was called.</Empty> : calls.map((c) => <ToolCard key={c.tool_id} c={c} />)}
      </Section>

      <Section title="Tables & graphs used" count={result.sources_used.length}>
        {result.sources_used.length === 0 ? (
          <Empty>No source was read.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Read via</th>
                  <th>By agent</th>
                </tr>
              </thead>
              <tbody>
                {result.sources_used.map((s) => (
                  <tr key={s.source}>
                    <td className="mono">{s.source}</td>
                    <td className="mono">{s.via.join(", ")}</td>
                    <td>
                      {s.specialists.map((a) => (
                        <AgentPill key={a} agent={a} />
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Fact cards" count={result.facts.length}>
        {result.facts.length === 0 ? (
          <Empty>No fact cards.</Empty>
        ) : (
          <ul className="facts">
            {result.facts.map((f) => (
              <li
                key={f.fact_id}
                ref={(el) => (factRefs.current[f.fact_id] = el)}
                className={`fact ${focusFact === f.fact_id ? "focus" : ""} ${(result.selected_fact_ids ?? []).includes(f.fact_id) ? "selected" : ""}`}
              >
                <div className="fact-head">
                  <span className="fid">{f.fact_id}</span>
                  <Pill>{f.topic}</Pill>
                  <span className="muted">from {f.tool_id}</span>
                </div>
                <p>{f.text}</p>
                {f.evidence_ids.length > 0 && (
                  <details>
                    <summary>
                      Source: {f.sources.join(" · ") || f.evidence_ids.join(", ")}
                    </summary>
                    {f.evidence_ids.map((id) => (
                      <SourceDetail key={id} id={id} source={evidence.get(id)} />
                    ))}
                  </details>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="stat">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

const STATUS_TONE: Record<string, string> = { ok: "green", empty: "slate", rejected: "red", error: "red", refused: "amber" };

function QueryCard({ q }: { q: QueryView }) {
  const [raw, setRaw] = useState(false);
  const where = q.language === "sql" ? q.datasets : [`${q.graph ?? "business"} graph`, ...(q.labels ?? []).map((l) => `:${l}`)];
  return (
    <article className={`query ${q.kept ? "kept" : ""}`}>
      <header>
        <AgentPill agent={q.agent} />
        <Pill tone={q.language === "sql" ? "blue" : "violet"}>{q.language.toUpperCase()}</Pill>
        {q.query_id && <span className="fid">{q.query_id}</span>}
        {q.round > 0 && <Pill tone="slate">round {q.round + 1}</Pill>}
        <Pill tone={STATUS_TONE[q.status] ?? "neutral"}>{q.status}</Pill>
        {q.kept && <Pill tone="green">kept as evidence</Pill>}
        {q.purpose && <span className="muted">{q.purpose.replace("_", " ")}</span>}
        <span className="spacer" />
        <span className="muted">
          {q.row_count !== null && q.row_count !== undefined ? `${q.row_count} row${q.row_count === 1 ? "" : "s"}${q.truncated ? " (truncated)" : ""} · ` : ""}
          {ms(q.duration_ms)}
        </span>
      </header>
      <div className="where">
        <span>{q.language === "sql" ? "Tables" : "Graph"}</span>
        {where.length ? where.map((w) => <code key={w}>{w}</code>) : <span className="muted">not reached (the guard stopped it)</span>}
      </div>
      {q.thought && <p className="thought">“{q.thought}”</p>}
      <div className="code-wrap">
        <button className="code-toggle" onClick={() => setRaw(!raw)} title="Toggle between formatted and exactly as executed">
          {raw ? "formatted" : "as executed"}
        </button>
        <pre className="code">{raw ? q.query : formatQuery(q.query, q.language)}</pre>
      </div>
      {q.message && <p className={`qmsg ${q.status === "ok" ? "" : "bad"}`}>{q.message}</p>}
      {q.kept && q.columns && q.rows && q.rows.length > 0 && (
        <details>
          <summary>
            Result rows ({q.rows.length}){q.fact_ids?.length ? ` · cited as ${q.fact_ids.join(", ")}` : ""}
          </summary>
          <div className="table-wrap">
            <table className="grid rows">
              <thead>
                <tr>
                  {q.columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {q.rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((v, j) => (
                      <td key={j}>{cell(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {q.records && q.records.length > 0 && <p className="muted small">Source records: {q.records.join(", ")}</p>}
        </details>
      )}
    </article>
  );
}

function ToolCard({ c }: { c: ToolCallView }) {
  const args = Object.entries(c.arguments).filter(([, v]) => v !== null && v !== undefined);
  return (
    <article className="query tool">
      <header>
        <AgentPill agent={c.agent} />
        <Pill tone="teal">certified</Pill>
        <code className="toolname">{c.tool}</code>
        <span className="fid">{c.tool_id}</span>
        <span className="spacer" />
        <span className="muted">
          {c.evidence_count} evidence · {ms(c.duration_ms)}
        </span>
      </header>
      <div className="where">
        <span>Arguments</span>
        {args.length ? args.map(([k, v]) => <code key={k}>{`${k}=${typeof v === "string" ? v : JSON.stringify(v)}`}</code>) : <span className="muted">defaults</span>}
      </div>
      {groupSources(c.sources).map(([kind, names]) => (
        <div className="where" key={kind}>
          <span>{kind}</span>
          {names.map((s) => (
            <code key={s}>{s}</code>
          ))}
        </div>
      ))}
      {c.fact_ids.length > 0 && <p className="muted small">Fact cards: {c.fact_ids.join(", ")}</p>}
    </article>
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
    <div className="source">
      <span className="fid">{id}</span>
      <pre className="code small">{JSON.stringify(rest, null, 2)}</pre>
    </div>
  );
}

function cell(v: unknown) {
  if (v === null || v === undefined) return <span className="muted">null</span>;
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}
