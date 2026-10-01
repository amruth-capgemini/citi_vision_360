// Types mirror backend/src/citi_project/api/response.py (the chat response contract).

export type Status = "answered" | "clarification" | "not_found" | "unsupported" | "unavailable";

export interface FactCard {
  fact_id: string;
  tool_id: string;
  topic: string;
  kind: string;
  text: string;
  evidence_ids: string[];
  sources: string[];
}

export interface EvidenceItem {
  evidence_id: string;
  source: Record<string, unknown>;
}

export type Note = { code?: string; message?: string; specialist?: string } | string;

export interface Flag {
  tool_id: string;
  value: unknown;
}

export interface SourceUsed {
  source: string;
  via: string[];
  specialists: string[];
}

export interface TraceEntry {
  node: string;
  status: string;
  duration_ms?: number;
  agent?: string;
  steps?: TraceEntry[];
  [key: string]: unknown;
}

export interface QueryView {
  agent: string;
  round: number;
  query_id: string | null;
  language: "sql" | "cypher";
  purpose: string | null;
  thought: string | null;
  status: string;
  message: string | null;
  row_count: number | null;
  duration_ms: number | null;
  query: string;
  datasets: string[];
  graph: string | null;
  kept: boolean;
  tool_id?: string;
  columns?: string[];
  rows?: unknown[][];
  truncated?: boolean;
  records?: string[];
  labels?: string[];
  namespace?: string;
  fact_ids?: string[];
}

export interface ToolCallView {
  tool_id: string;
  tool: string;
  agent: string;
  arguments: Record<string, unknown>;
  namespace: string;
  status: string;
  duration_ms: number | null;
  sources: string[];
  evidence_count: number;
  fact_ids: string[];
}

export interface AgentView {
  agent: string;
  role: "supervisor" | "specialist";
  status: string;
  tools: string[];
  queries_run: number;
  queries_kept: number;
  duration_ms: number;
  used: boolean;
}

export interface Narrative {
  summary: string;
  paragraphs: { heading: string; text: string; fact_ids: string[] }[];
}

export interface ChatResponse {
  session_id: string;
  question: string;
  status: Status;
  final_answer: string;
  narrative?: Narrative | null;
  facts: FactCard[];
  evidence: EvidenceItem[];
  flags: Flag[];
  limitations: Note[];
  specialists_used: string[];
  sources_used: SourceUsed[];
  selected_fact_ids?: string[] | null;
  resolved_entities?: Record<string, unknown> | null;
  understanding?: Understanding | null;
  trace: TraceEntry[];
  memory?: Memory;
  evidence_view: { agents: AgentView[]; tool_calls: ToolCallView[]; queries: QueryView[] };
}

/** How the supervisor read the question (backend graph.Orchestrator.understand), and why it stopped if it did. */
export interface Understanding {
  standalone_question: string;
  scope: "focus" | "named" | "portfolio";
  entities: { type: string; mention: string }[];
  requested: { concept: string; type: string | null; attribute: string | null; recorded: boolean | null; restricted: boolean }[];
  assumption: string | null;
  clarifying_question: string | null;
  notes: string[];
  reason?: string;
  explanation?: string | null;
}

/** What the session remembers after a turn (backend ConversationState.context). */
export interface Memory {
  active_vendor_id: string | null;
  active_contract_id: string | null;
  recent_turns: { question: string; status: string; vendor_ids: string[]; contract_ids: string[]; summary: string }[];
}

export interface Health {
  status: "ok" | "degraded";
  checks: Record<string, { ok: boolean; [key: string]: unknown }>;
}

export interface CatalogSummary {
  systems: { system: string; datasets: number; rows: number }[];
  datasets: { dataset: string; system: string; rows: number | null; grain: string | null; fields: number; domains: string[] }[];
  domains: { domain: string; datasets: string[] }[];
}

/** Mirrors backend/src/citi_project/api/dashboard.py. Money fields are exact USD decimal strings. */
export type Attention = "expired" | "past_notice" | "notice_due" | "expiring" | "on_track";

export interface Variance {
  amount: string;
  percent: string | null;
}

export interface DashboardContract {
  vendor_id: string;
  vendor_name: string | null;
  contract_id: string;
  description: string | null;
  organization: string | null;
  end_date: string | null;
  days_to_expiry: number | null;
  renewal_decision_date: string | null;
  days_to_decision: number | null;
  notice_days: number | null;
  automatic_renewal: boolean | null;
  attention: Attention;
  risk_tier: string | null;
  risk_assessment_status: string | null;
  risk_assessment_date: string | null;
  vrm_status: string | null;
  sla: { period: string | null; actual_percent: string | null; target_percent: string | null; breach: boolean };
  budget_2026: string | null;
  forecast_2026: string | null;
  actual_ytd_2026: string | null;
  forecast_variance: Variance | null;
  warning: string | null;
  source: { dataset: string; record_id: string | null; renewal_terms: string | null };
}

export interface ReviewItem {
  vendor_id: string;
  vendor_name: string | null;
  contract_id: string;
  priority: "high" | "medium";
  status: "draft";
  reasons: string[];
  actions: string[];
  headline: string;
}

export interface Dashboard {
  as_of_date: string;
  scanned_at: string;
  attention_days: number;
  totals: {
    vendors: number;
    contracts: number;
    budget_2026: string;
    forecast_2026: string;
    actual_ytd_2026: string;
    forecast_variance: Variance | null;
    past_notice: number;
    decisions_due: number;
    expiring: number;
    high_risk: number;
    missing_assessment: number;
    sla_breaches: number;
    over_budget: number;
    in_review: number;
  };
  contracts: DashboardContract[];
  review: ReviewItem[];
  sources: string[];
  limitations: Note[];
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return (await response.json()) as T;
}

export interface StreamHandlers {
  onNode: (entry: TraceEntry) => void;
  onStep: (step: TraceEntry) => void;
  onResult: (result: ChatResponse) => void;
  onError: (message: string, result?: ChatResponse) => void;
}

/** Ask one question over SSE; returns a function that cancels the stream. */
export function askStream(question: string, sessionId: string, handlers: StreamHandlers): () => void {
  const params = new URLSearchParams({ question, session_id: sessionId });
  const source = new EventSource(`/api/chat/stream?${params}`);
  let finished = false;
  const done = () => {
    finished = true;
    source.close();
  };
  source.addEventListener("node", (e) => handlers.onNode(JSON.parse((e as MessageEvent).data)));
  source.addEventListener("step", (e) => handlers.onStep(JSON.parse((e as MessageEvent).data)));
  source.addEventListener("result", (e) => {
    done();
    handlers.onResult(JSON.parse((e as MessageEvent).data));
  });
  source.addEventListener("error", (e) => {
    const data = (e as MessageEvent).data;
    if (finished) return;
    done();
    if (data) {
      const result = JSON.parse(data) as ChatResponse;
      handlers.onError(result.final_answer, result);
    } else {
      handlers.onError("The connection to the API was lost. Is citi-api running?");
    }
  });
  return done;
}

export function noteText(note: Note): string {
  return typeof note === "string" ? note : note.message ?? note.code ?? JSON.stringify(note);
}

export function flagText(flag: Flag): string {
  const v = flag.value as Record<string, unknown> | string;
  if (typeof v === "string") return v;
  if (v && typeof v === "object") {
    const head = [v.vendor_id, v.contract_id, v.code].filter(Boolean).join(" ");
    const message = (v.message ?? v.description) as string | undefined;
    return [head, message].filter(Boolean).join(": ") || JSON.stringify(v);
  }
  return String(v);
}
