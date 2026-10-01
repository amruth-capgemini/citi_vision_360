import { useEffect, useRef, useState } from "react";
import { ArrowRightIcon, ArrowUpIcon, BrainIcon, MessageSquareTextIcon, PlusIcon } from "lucide-react";
import type { ChatResponse } from "../api";
import { askStream, getJson } from "../api";
import { addNode, addStep, fromTrace, type TimelineItem } from "../timeline";
import { Answer } from "../components/Answer";
import { AgentsPanel } from "../components/AgentsPanel";
import { EvidencePanel } from "../components/EvidencePanel";
import { PageHeader } from "../components/PageHeader";
import { SourcesPanel } from "../components/SourcesPanel";
import { EmptyNote, StatusBadge, ms } from "../components/common";
import { newSessionId, storeSession, storedSession } from "@/lib/session";
import { Badge } from "@/components/ui/badge";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupText, InputGroupTextarea } from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker";
import { Message, MessageContent, MessageHeader } from "@/components/ui/message";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

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

/** Scenarios: the free-form decision chat, with the agents, evidence and sources behind each answer. */
export function ScenariosPage() {
  const [sessionId, setSessionId] = useState(storedSession);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("agents");
  const [focusFact, setFocusFact] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [examples, setExamples] = useState<string[]>(FALLBACK_EXAMPLES);
  const cancel = useRef<(() => void) | null>(null);
  const autoAsked = useRef(false);

  const running = turns.some((t) => !t.result && !t.error);
  const current = turns.find((t) => t.id === selected) ?? turns[turns.length - 1];

  useEffect(() => {
    getJson<{ examples: string[] }>("/api/examples").then((r) => setExamples(r.examples), () => undefined);
    // Demo links: ?q=<question> asks it on load (once, even under StrictMode's double effects).
    const q = new URLSearchParams(window.location.search).get("q");
    if (q && !autoAsked.current) {
      autoAsked.current = true;
      ask(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    storeSession(id);
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
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Scenarios">
        <Button variant="outline" size="sm" onClick={newChat}>
          <PlusIcon data-icon="inline-start" />
          New chat
        </Button>
      </PageHeader>

      <div className="grid min-h-0 flex-1 grid-cols-1 max-lg:overflow-y-auto lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <section className="flex min-h-0 flex-col max-lg:min-h-[70vh] lg:border-r">
          {turns.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <MessageSquareTextIcon />
                </EmptyMedia>
                <EmptyTitle>Ask about vendors, contracts, spend, risk or workforce</EmptyTitle>
                <EmptyDescription>Every answer shows which agents ran, the SQL and Cypher they used, and the source of every fact.</EmptyDescription>
              </EmptyHeader>
              <EmptyContent className="max-w-xl flex-row flex-wrap justify-center gap-2">
                {examples.map((q) => (
                  <Button key={q} variant="outline" size="sm" onClick={() => ask(q)}>
                    {q}
                  </Button>
                ))}
              </EmptyContent>
            </Empty>
          ) : (
            <MessageScrollerProvider autoScroll>
              <MessageScroller className="flex-1">
                <MessageScrollerViewport>
                  <MessageScrollerContent className="p-5">
                    {turns.map((t) => [
                      <MessageScrollerItem key={`${t.id}-q`} messageId={`${t.id}-q`} scrollAnchor>
                        <Message align="end">
                          <MessageContent>
                            <Bubble align="end">
                              <BubbleContent className="whitespace-pre-wrap">{t.question}</BubbleContent>
                            </Bubble>
                          </MessageContent>
                        </Message>
                      </MessageScrollerItem>,
                      <MessageScrollerItem key={`${t.id}-a`} messageId={`${t.id}-a`}>
                        <BotMessage
                          turn={t}
                          shown={turns.length > 1 && current?.id === t.id}
                          onSelect={() => setSelected(t.id)}
                          onEvidence={() => {
                            setSelected(t.id);
                            setTab("evidence");
                          }}
                          onFact={(f) => showFact(t.id, f)}
                        />
                      </MessageScrollerItem>,
                    ])}
                  </MessageScrollerContent>
                </MessageScrollerViewport>
                <MessageScrollerButton />
              </MessageScroller>
            </MessageScrollerProvider>
          )}

          <ContextBar turns={turns} />
          <form
            className="border-t px-5 pt-3 pb-4"
            onSubmit={(e) => {
              e.preventDefault();
              ask(input);
            }}
          >
            <InputGroup>
              <InputGroupTextarea
                value={input}
                placeholder="Ask a question, e.g. What contracts are at major risk?"
                aria-label="Question"
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
              <InputGroupAddon align="block-end">
                <InputGroupText className="text-xs">
                  <Kbd>Enter</Kbd> to ask · <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd> for a new line
                </InputGroupText>
                <InputGroupButton type="submit" variant="default" size="sm" className="ml-auto" disabled={running || !input.trim()}>
                  {running ? <Spinner data-icon="inline-start" /> : <ArrowUpIcon data-icon="inline-start" />}
                  {running ? "Working…" : "Ask"}
                </InputGroupButton>
              </InputGroupAddon>
            </InputGroup>
          </form>
        </section>

        <aside className="flex min-h-0 flex-col max-lg:min-h-[70vh] max-lg:border-t">
          <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="min-h-0 flex-1 gap-0">
            <div className="border-b px-4 pt-2 pb-1">
              <TabsList variant="line">
                <TabsTrigger value="agents">Agents</TabsTrigger>
                <TabsTrigger value="evidence">
                  Evidence
                  {current?.result && (
                    <Badge variant="secondary">{current.result.evidence_view.queries.length + current.result.evidence_view.tool_calls.length}</Badge>
                  )}
                </TabsTrigger>
                <TabsTrigger value="sources">Sources</TabsTrigger>
              </TabsList>
            </div>
            {(["agents", "evidence", "sources"] as Tab[]).map((name) => (
              <TabsContent key={name} value={name} className="min-h-0">
                <ScrollArea className="h-full">
                  <div className="flex flex-col gap-3 px-5 py-4">
                    {current && name !== "sources" && <p className="text-muted-foreground italic">“{current.question}”</p>}
                    {name === "agents" &&
                      (current ? (
                        <AgentsPanel items={current.timeline} running={!current.result && !current.error} />
                      ) : (
                        <EmptyNote>Ask a question to see the agents at work.</EmptyNote>
                      ))}
                    {name === "evidence" &&
                      (current?.result ? (
                        <EvidencePanel result={current.result} focusFact={focusFact} />
                      ) : (
                        <EmptyNote>{current ? "Evidence appears when the answer is ready." : "Ask a question to see its evidence."}</EmptyNote>
                      ))}
                    {name === "sources" && <SourcesPanel />}
                  </div>
                </ScrollArea>
              </TabsContent>
            ))}
          </Tabs>
        </aside>
      </div>
    </div>
  );
}

function BotMessage({
  turn: t,
  shown,
  onSelect,
  onEvidence,
  onFact,
}: {
  turn: Turn;
  shown: boolean;
  onSelect: () => void;
  onEvidence: () => void;
  onFact: (factId: string) => void;
}) {
  return (
    <Message align="start" onClick={onSelect}>
      <MessageContent>
        <MessageHeader className="gap-2">
          <StatusBadge status={t.result ? t.result.status : t.error ? "unavailable" : "running"} />
          {t.elapsed !== undefined && <span className="tabular-nums">{ms(Math.round(t.elapsed))}</span>}
          {shown && <Badge variant="outline">shown in panel</Badge>}
          {t.result && (
            <Button
              variant="link"
              size="xs"
              className="ml-auto"
              onClick={(e) => {
                e.stopPropagation();
                onEvidence();
              }}
            >
              Evidence
              <ArrowRightIcon data-icon="inline-end" />
            </Button>
          )}
        </MessageHeader>
        <Bubble variant={t.error && !t.result ? "destructive" : "outline"} className="max-w-full">
          <BubbleContent className="w-full px-4 py-3">
            {t.result ? (
              <Answer result={t.result} onFact={onFact} />
            ) : t.error ? (
              t.error
            ) : (
              <Working items={t.timeline} />
            )}
          </BubbleContent>
        </Bubble>
      </MessageContent>
    </Message>
  );
}

function Working({ items }: { items: TimelineItem[] }) {
  const last = items[items.length - 1];
  const agents = [...new Set(items.map((i) => i.agent).filter(Boolean))];
  return (
    <div className="flex items-center gap-2.5">
      <Spinner />
      <span>
        <span className="shimmer">{last ? `${last.node === "explore" && last.live ? `${last.agent} exploring sources` : last.node}…` : "Routing the question…"}</span>
        {agents.length > 0 && <span className="text-muted-foreground"> · {agents.join(", ")}</span>}
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
    <div className="border-t bg-muted/50 px-5 py-2">
      <Marker className="flex-wrap text-xs">
        <MarkerIcon>
          <BrainIcon />
        </MarkerIcon>
        <MarkerContent className="flex flex-wrap items-center gap-1.5">
          <span>In context</span>
          {vendor ? <Badge variant="secondary" className="font-mono">{vendor}</Badge> : <span>no vendor yet</span>}
          {contract && <Badge variant="secondary" className="font-mono">{contract}</Badge>}
          <Tooltip>
            <TooltipTrigger render={<span className="cursor-help underline decoration-dotted underline-offset-3" />}>
              remembers {recent.length} turn{recent.length === 1 ? "" : "s"}
            </TooltipTrigger>
            <TooltipContent className="flex-col items-start">
              {recent.map((t, i) => (
                <span key={i}>• {t.question}</span>
              ))}
            </TooltipContent>
          </Tooltip>
          <span>· “this vendor”, “this contract” and “its” refer here</span>
        </MarkerContent>
      </Marker>
    </div>
  );
}
