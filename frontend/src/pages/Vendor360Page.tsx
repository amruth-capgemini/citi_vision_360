import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CircleAlertIcon, XIcon } from "lucide-react";
import type { ChatResponse, Dashboard } from "../api";
import { askStream, getJson } from "../api";
import { PageHeader } from "../components/PageHeader";
import { AnswerCard, PendingAnswer, answerSummary, specialistLabel } from "../components/vendor360/AnswerCard";
import { ContractDashboard } from "../components/vendor360/ContractDashboard";
import { HumanReview, type ReviewEntry } from "../components/vendor360/HumanReview";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { newSessionId } from "@/lib/session";

const SUGGESTIONS = [
  "Should we renew Aurelix Codeworks?",
  "Why is Lumenquay Identity Systems above budget?",
  "How dependent are we on Veylora Application Services?",
  "What contracts are at major risk?",
];
// The server rescans once a day; checking hourly picks that up without forcing a scan.
const POLL_MS = 60 * 60 * 1000;

interface Asked {
  question: string;
  result: ChatResponse | null;
  error: string | null;
  stage: string | null;
}

/**
 * Vendor 360. Before a question: the contract dashboard, then the portfolio's human review queue.
 * After one: the answer, the human review of what it drafted, then the dashboard.
 */
export function Vendor360Page() {
  const [sessionId] = useState(newSessionId);
  const [input, setInput] = useState("");
  const [asked, setAsked] = useState<Asked | null>(null);
  const cancel = useRef<(() => void) | null>(null);
  const { dashboard, error: dashboardError, scanning, rescan, reload } = useDashboard();
  const running = asked !== null && asked.result === null && asked.error === null;

  useEffect(() => () => cancel.current?.(), []);

  function ask(question: string) {
    const text = question.trim();
    if (!text || running) return;
    setInput(text);
    setAsked({ question: text, result: null, error: null, stage: null });
    cancel.current = askStream(text, sessionId, {
      onNode: (entry) => setAsked((a) => a && { ...a, stage: String(entry.node) }),
      onStep: () => undefined,
      onResult: (result) => setAsked((a) => a && { ...a, result }),
      onError: (message, result) => setAsked((a) => a && { ...a, error: message, result: result ?? null }),
    });
  }

  function clear() {
    cancel.current?.();
    setAsked(null);
    setInput("");
  }

  const portfolio = useMemo(() => portfolioEntries(dashboard), [dashboard]);
  const related = useMemo(() => (asked?.result && dashboard ? relatedEntries(asked.result, dashboard) : []), [asked?.result, dashboard]);

  const board = dashboardError ? (
    <Alert variant="destructive">
      <CircleAlertIcon />
      <AlertTitle>The dashboard could not be loaded</AlertTitle>
      <AlertDescription>{dashboardError}</AlertDescription>
      <AlertAction>
        <Button variant="outline" size="sm" onClick={reload}>
          Retry
        </Button>
      </AlertAction>
    </Alert>
  ) : dashboard ? (
    <ContractDashboard dashboard={dashboard} scanning={scanning} onRescan={rescan} />
  ) : (
    <Skeleton className="h-96 w-full rounded-xl" />
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Vendor 360" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-6 py-8">
          <h2 className="font-heading text-4xl font-medium tracking-tight">Vendor review</h2>

          <div className="flex flex-col gap-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                ask(input);
              }}
            >
              <InputGroup className="h-11 bg-background">
                <InputGroupAddon>
                  <InputGroupText className="text-xs max-sm:hidden">Ask a vendor question</InputGroupText>
                </InputGroupAddon>
                <InputGroupInput
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Should we renew, renegotiate, consolidate, or reduce dependency?"
                  aria-label="Vendor question"
                  maxLength={2000}
                />
                <InputGroupAddon align="inline-end">
                  {asked && !running && (
                    <InputGroupButton size="icon-xs" aria-label="Clear the question" onClick={clear}>
                      <XIcon />
                    </InputGroupButton>
                  )}
                  <InputGroupButton type="submit" variant="default" size="sm" disabled={running || !input.trim()}>
                    {running && <Spinner data-icon="inline-start" />}
                    Ask
                  </InputGroupButton>
                </InputGroupAddon>
              </InputGroup>
            </form>
            {!asked && (
              <div className="flex flex-wrap gap-2">
                {SUGGESTIONS.map((q) => (
                  <Button key={q} variant="outline" size="sm" onClick={() => ask(q)}>
                    {q}
                  </Button>
                ))}
              </div>
            )}
          </div>

          {asked ? (
            <>
              {asked.result ? (
                <AnswerCard question={asked.question} result={asked.result} error={asked.error} />
              ) : asked.error ? (
                <Alert variant="destructive">
                  <CircleAlertIcon />
                  <AlertTitle>The question could not be answered</AlertTitle>
                  <AlertDescription>{asked.error}</AlertDescription>
                </Alert>
              ) : (
                <PendingAnswer question={asked.question} stage={asked.stage} />
              )}
              {asked.result && <HumanReview entries={related} draft={answerSummary(asked.result)} specialist={specialistLabel(asked.result)} />}
              {board}
            </>
          ) : (
            <>
              {board}
              {dashboard && <HumanReview entries={portfolio} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** The daily portfolio scan. Rescan forces the server to re-read PostgreSQL and the graph. */
function useDashboard() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  const load = useCallback((refresh: boolean) => {
    if (refresh) setScanning(true);
    getJson<Dashboard>(`/api/dashboard${refresh ? "?refresh=true" : ""}`)
      .then((d) => {
        setDashboard(d);
        setError(null);
      })
      .catch(() => setError("The dashboard sources (PostgreSQL and the graph) are unavailable. Is citi-api running?"))
      .finally(() => setScanning(false));
  }, []);

  useEffect(() => {
    load(false);
    const timer = window.setInterval(() => load(false), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  return { dashboard, error, scanning, rescan: () => load(true), reload: () => load(false) };
}

function portfolioEntries(dashboard: Dashboard | null): ReviewEntry[] {
  if (!dashboard) return [];
  const contracts = new Map(dashboard.contracts.map((c) => [c.contract_id, c]));
  return dashboard.review.flatMap((item) => {
    const contract = contracts.get(item.contract_id);
    return contract ? [{ contract, item }] : [];
  });
}

/** The contracts an answer is about: its resolved entities, plus vendor and contract IDs or names in its text and facts. */
function relatedEntries(result: ChatResponse, dashboard: Dashboard): ReviewEntry[] {
  const resolved = (result.resolved_entities ?? {}) as { vendor_ids?: string[]; contract_ids?: string[] };
  const text = [result.final_answer, ...result.facts.map((f) => f.text)].join("\n");
  const ids = new Set([...(resolved.vendor_ids ?? []), ...(resolved.contract_ids ?? [])]);
  for (const match of text.matchAll(/\b(V-\d{3}|CTR-\d{3})\b/g)) ids.add(match[1]);
  const review = new Map(dashboard.review.map((r) => [r.contract_id, r]));
  return dashboard.contracts
    .filter((c) => ids.has(c.vendor_id) || ids.has(c.contract_id) || (c.vendor_name !== null && text.includes(c.vendor_name)))
    .map((contract) => ({ contract, item: review.get(contract.contract_id) ?? null }))
    .sort((a, b) => rank(a) - rank(b));
}

function rank(entry: ReviewEntry) {
  return entry.item ? (entry.item.priority === "high" ? 0 : 1) : 2;
}
