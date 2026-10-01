import type { TraceEntry } from "../api";
import type { TimelineItem } from "../timeline";
import { detail, label } from "../timeline";
import { AgentPill, Empty, Pill, ms } from "./common";

/** Live timeline: supervisor -> specialists -> tools -> explorers -> sources, with durations. */
export function AgentsPanel({ items, running }: { items: TimelineItem[]; running: boolean }) {
  if (items.length === 0) return <Empty>{running ? "Starting…" : "Ask a question to see the agents at work."}</Empty>;
  const longest = Math.max(1, ...items.map((i) => i.duration_ms ?? 0));
  return (
    <ol className="timeline">
      {items.map((item) => (
        <li key={item.key} className={`tl ${item.status} ${item.live ? "live" : ""}`}>
          <span className="dot" />
          <div className="tl-body">
            <div className="tl-head">
              <strong>{label(item)}</strong>
              <AgentPill agent={item.agent} />
              {item.status === "stop" && <Pill tone="red">stop</Pill>}
              <span className="spacer" />
              <span className="muted">{item.live ? "running…" : ms(item.duration_ms)}</span>
            </div>
            {!item.live && item.duration_ms !== undefined && (
              <div className="bar">
                <span style={{ width: `${Math.max(2, (100 * item.duration_ms) / longest)}%` }} />
              </div>
            )}
            <p className="muted small">{detail(item)}</p>
            {item.steps.length > 0 && (
              <ul className="steps">
                {item.steps.map((s, i) => (
                  <Step key={i} s={s} />
                ))}
              </ul>
            )}
          </div>
        </li>
      ))}
      {running && (
        <li className="tl live">
          <span className="dot" />
          <div className="tl-body muted">working…</div>
        </li>
      )}
    </ol>
  );
}

function Step({ s }: { s: TraceEntry }) {
  const action = String(s.action ?? s.node);
  const where = (s.datasets as string[] | undefined)?.join(", ") || (s.graph ? `${s.graph} graph` : "");
  return (
    <li className={`step ${s.status}`}>
      <div className="tl-head">
        <code>{s.query_id ? `${s.query_id} ` : ""}{action}</code>
        {s.purpose ? <span className="muted">{String(s.purpose).replace("_", " ")}</span> : null}
        <Pill tone={s.status === "ok" ? "green" : s.status === "empty" ? "slate" : "red"}>{String(s.status)}</Pill>
        {s.row_count !== undefined && <span className="muted">{String(s.row_count)} rows</span>}
        <span className="spacer" />
        <span className="muted">{ms(s.duration_ms as number | undefined)}</span>
      </div>
      {where && <p className="small mono">{where}</p>}
      {s.thought ? <p className="small muted">“{String(s.thought)}”</p> : null}
      {s.message ? <p className="small bad">{String(s.message)}</p> : null}
    </li>
  );
}
