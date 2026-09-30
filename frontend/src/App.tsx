import { useEffect, useRef, useState } from "react";
import type { ChatResponse, Health } from "./api";
import { askStream, getJson } from "./api";
import { addNode, addStep, fromTrace, type TimelineItem } from "./timeline";
import { Answer } from "./components/Answer";
import { AgentsPanel } from "./components/AgentsPanel";
import { EvidencePanel } from "./components/EvidencePanel";
import { SourcesPanel } from "./components/SourcesPanel";
import { StatusBadge, ms } from "./components/common";

interface Turn {
  id: string;
  question: string;
  timeline: TimelineItem[];
  result?: ChatResponse;
  error?: string;
  started: number;
  elapsed?: number;
}

type Tab = "agents" | "evidence" | "sources";

const FALLBACK_EXAMPLES = [
  "What do we know about Aurelix Codeworks?",
  "Which contracts expire in the next 90 days?",
  "What contracts are at major risk?",
  "Why is V-005 above budget?",
];

function newSessionId() {
  return (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
}

function storedSession() {
  try {
    const existing = sessionStorage.getItem("citi-session");
    if (existing) return existing;
    const id = newSessionId();
    sessionStorage.setItem("citi-session", id);
    return id;
  } catch {
    return newSessionId();
  }
}

export default function App() {
  const [sessionId, setSessionId] = useState(storedSession);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("agents");
  const [focusFact, setFocusFact] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [examples, setExamples] = useState<string[]>(FALLBACK_EXAMPLES);
  const [health, setHealth] = useState<Health | null>(null);
  const cancel = useRef<(() => void) | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const autoAsked = useRef(false);

  const running = turns.some((t) => !t.result && !t.error);
  const current = turns.find((t) => t.id === selected) ?? turns[turns.length - 1];

  useEffect(() => {
    getJson<{ examples: string[] }>("/api/examples").then((r) => setExamples(r.examples), () => undefined);
    getJson<Health>("/api/health").then(setHealth, () => setHealth(null));
    // Demo links: ?q=<question> asks it on load (once, even under StrictMode's double effects).
    const q = new URLSearchParams(window.location.search).get("q");
    if (q && !autoAsked.current) {
      autoAsked.current = true;
      ask(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, which React would call as a cleanup.
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns.length, current?.result]);

  const update = (id: string, change: (t: Turn) => Turn) => setTurns((all) => all.map((t) => (t.id === id ? change(t) : t)));

  function ask(question: string) {
    const text = question.trim();
    if (!text || running) return;
    const id = newSessionId();
    setTurns((all) => [...all, { id, question: text, timeline: [], started: performance.now() }]);
    setSelected(id);
    setTab("agents");
    setFocusFact(null);
    setInput("");
    cancel.current = askStream(text, sessionId, {
      onNode: (entry) => update(id, (t) => ({ ...t, timeline: addNode(t.timeline, entry) })),
      onStep: (step) => update(id, (t) => ({ ...t, timeline: addStep(t.timeline, step) })),
      onResult: (result) => {
        update(id, (t) => ({ ...t, result, timeline: fromTrace(result.trace), elapsed: performance.now() - t.started }));
        setTab("evidence");
      },
      onError: (message, result) => update(id, (t) => ({ ...t, error: message, result, elapsed: performance.now() - t.started })),
    });
  }

  function newChat() {
    cancel.current?.();
    const id = newSessionId();
    try {
      sessionStorage.setItem("citi-session", id);
    } catch {
      /* per-tab convenience only */
    }
    setSessionId(id);
    setTurns([]);
    setSelected(null);
  }

  function showFact(turnId: string, factId: string) {
    setSelected(turnId);
    setTab("evidence");
    setFocusFact(factId);
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◆</span>
          <div>
            <h1>Vendor Decision Intelligence</h1>
            <p>Read-only decision context over the synthetic 20-vendor pack · snapshot 2026-09-28</p>
          </div>
        </div>
        <div className="top-right">
          <HealthDots health={health} />
          <button className="ghost" onClick={newChat}>
            New chat
          </button>
        </div>
      </header>

      <main className="layout">
        <section className="chat">
          <div className="messages">
            {turns.length === 0 && (
              <div className="welcome">
                <h2>Ask about vendors, contracts, spend, risk or workforce</h2>
                <p className="muted">Every answer shows which agents ran, the SQL and Cypher they used, and the source of every fact.</p>
                <div className="examples">
                  {examples.map((q) => (
                    <button key={q} className="example" onClick={() => ask(q)}>
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {turns.map((t) => (
              <div key={t.id} className={`turn ${current?.id === t.id ? "active" : ""}`} onClick={() => setSelected(t.id)}>
                <div className="bubble user">{t.question}</div>
                <div className="bubble bot">
                  <div className="bot-head">
                    <StatusBadge status={t.result ? t.result.status : t.error ? "unavailable" : "running"} />
                    {t.elapsed !== undefined && <span className="muted small">{ms(Math.round(t.elapsed))}</span>}
                    <span className="spacer" />
                    {t.result && (
                      <button
                        className="link"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelected(t.id);
                          setTab("evidence");
                        }}
                      >
                        Evidence →
                      </button>
                    )}
                  </div>
                  {t.result ? (
                    <Answer result={t.result} onFact={(f) => showFact(t.id, f)} />
                  ) : t.error ? (
                    <p className="bad">{t.error}</p>
                  ) : (
                    <Progress items={t.timeline} />
                  )}
                </div>
              </div>
            ))}
            <div ref={bottom} />
          </div>

          <ContextBar turns={turns} />
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              ask(input);
            }}
          >
            <textarea
              value={input}
              placeholder="Ask a question, e.g. What contracts are at major risk?"
              maxLength={2000}
              rows={2}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  ask(input);
                }
              }}
            />
            <button type="submit" disabled={running || !input.trim()}>
              {running ? "Working…" : "Ask"}
            </button>
          </form>
        </section>

        <aside className="panel">
          <nav className="tabs" role="tablist">
            {(["agents", "evidence", "sources"] as Tab[]).map((name) => (
              <button key={name} role="tab" aria-selected={tab === name} className={tab === name ? "on" : ""} onClick={() => setTab(name)}>
                {name === "agents" ? "Agents" : name === "evidence" ? "Evidence" : "Sources"}
                {name === "evidence" && current?.result && (
                  <span className="count">{current.result.evidence_view.queries.length + current.result.evidence_view.tool_calls.length}</span>
                )}
              </button>
            ))}
          </nav>
          <div className="panel-body">
            {current && tab !== "sources" && <p className="panel-q">“{current.question}”</p>}
            {tab === "agents" &&
              (current ? <AgentsPanel items={current.timeline} running={!current.result && !current.error} /> : <p className="empty">Ask a question to see the agents at work.</p>)}
            {tab === "evidence" &&
              (current?.result ? (
                <EvidencePanel result={current.result} focusFact={focusFact} />
              ) : (
                <p className="empty">{current ? "Evidence appears when the answer is ready." : "Ask a question to see its evidence."}</p>
              ))}
            {tab === "sources" && <SourcesPanel />}
          </div>
        </aside>
      </main>
    </div>
  );
}

function Progress({ items }: { items: TimelineItem[] }) {
  const last = items[items.length - 1];
  const agents = [...new Set(items.map((i) => i.agent).filter(Boolean))];
  return (
    <div className="progress">
      <span className="spinner" />
      <span>
        {last ? `${last.node === "explore" && last.live ? `${last.agent} exploring sources` : last.node}…` : "Routing the question…"}
        {agents.length > 0 && <span className="muted"> · {agents.join(", ")}</span>}
      </span>
    </div>
  );
}

/** The session's memory after the latest answer: what 'this vendor' / 'this contract' refer to. */
function ContextBar({ turns }: { turns: Turn[] }) {
  const memory = [...turns].reverse().find((t) => t.result?.memory)?.result?.memory;
  if (!memory) return null;
  const { active_vendor_id: vendor, active_contract_id: contract, recent_turns: recent } = memory;
  return (
    <div className="contextbar" title={recent.map((t) => `• ${t.question}`).join("\n")}>
      <span className="muted">In context</span>
      {vendor ? <code>{vendor}</code> : <span className="muted">no vendor yet</span>}
      {contract && <code>{contract}</code>}
      <span className="muted small">
        · remembers {recent.length} turn{recent.length === 1 ? "" : "s"} · “this vendor”, “this contract” and “its” refer here
      </span>
    </div>
  );
}

function HealthDots({ health }: { health: Health | null }) {
  if (!health) return <span className="health muted small">API offline?</span>;
  return (
    <span className="health">
      {Object.entries(health.checks).map(([name, check]) => (
        <span key={name} className={`hdot ${check.ok ? "ok" : "bad"}`} title={`${name}: ${check.ok ? "ok" : "unavailable"}`}>
          {name}
        </span>
      ))}
    </span>
  );
}
