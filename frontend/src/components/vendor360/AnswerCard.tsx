import { ChevronDownIcon, CircleAlertIcon } from "lucide-react";
import type { ChatResponse } from "../../api";
import { flagText, noteText } from "../../api";
import { Interpretation, stripCitations, stripNotes } from "../Answer";
import { StatusBadge } from "../common";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";

export const SPECIALIST: Record<string, string> = {
  vendor360: "Vendor 360 specialist",
  renewal: "Renewal review specialist",
  risk_dependency: "Risk & dependency specialist",
  rationalization: "Rationalization specialist",
  spend_forecast: "Spend forecast specialist",
  what_if: "Scenario specialist",
};

// Standing caveats (synthetic data, POC sample, how rows were gathered) that would appear on every answer.
// Period notes ("Actual is January-August") and missing-data notes are still shown.
const BOILERPLATE = new Set([
  "synthetic_data",
  "representative_workforce",
  "exploration_supplementary",
  "exploration_rows",
  "answer_examples",
  "unreviewed_link",
  "duplicate_spend_representation",
]);

export function specialistLabel(result: ChatResponse) {
  const names = result.specialists_used.map((a) => SPECIALIST[a] ?? a);
  return names.length ? names.join(" · ") : "Decision supervisor";
}

/** The answer's headline text, without host citations or the appended notes. */
export function answerSummary(result: ChatResponse) {
  if (result.status === "answered" && result.narrative) return stripCitations(result.narrative.summary);
  return result.status === "answered" ? stripNotes(result.final_answer) : result.final_answer;
}

export function PendingAnswer({ question, stage }: { question: string; stage: string | null }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription className="flex items-center gap-2">
          <Spinner />
          <span className="shimmer">{stage ? `${stage}…` : "Routing the question…"}</span>
        </CardDescription>
        <CardTitle className="text-xl">“{question}”</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <Skeleton className="h-5 w-full" />
        <Skeleton className="h-5 w-5/6" />
        <Skeleton className="h-5 w-2/3" />
      </CardContent>
    </Card>
  );
}

/** The evidence-backed answer to one question; evidence and sources fold away below it. */
export function AnswerCard({ question, result, error }: { question: string; result: ChatResponse; error: string | null }) {
  const narrative = result.status === "answered" ? result.narrative : null;
  const picked = new Set(result.selected_fact_ids ?? []);
  const facts = picked.size > 0 ? result.facts.filter((f) => picked.has(f.fact_id)) : result.facts;
  const limitations = [...new Set(result.limitations.filter((n) => typeof n === "string" || !BOILERPLATE.has(n.code ?? "")).map(noteText))];

  return (
    <Card>
      <CardHeader>
        <CardDescription className="text-2xl font-semibold tracking-wide text-primary uppercase">{specialistLabel(result)}</CardDescription>
        <CardTitle className="text-x">Evidence-backed response</CardTitle>
        <CardDescription>“{question}”</CardDescription>
        <CardAction>{result.status === "answered" ? <Badge variant="outline">Draft</Badge> : <StatusBadge status={result.status} />}</CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {error && (
          <Alert variant="destructive">
            <CircleAlertIcon />
            <AlertTitle>The answer is incomplete</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <p className="font-heading text-lg leading-relaxed whitespace-pre-wrap">{answerSummary(result)}</p>
        <Interpretation result={result} />
        {narrative && narrative.paragraphs.length > 0 && (
          <div className="flex flex-col gap-3">
            {narrative.paragraphs.map((p, i) => (
              <div key={i} className="flex flex-col gap-1">
                <h3 className="font-heading text-sm font-semibold">{p.heading}</h3>
                <p className="leading-relaxed text-muted-foreground">{stripCitations(p.text)}</p>
              </div>
            ))}
          </div>
        )}
        <Collapsible className="flex flex-col gap-4">
          <CollapsibleTrigger render={<Button variant="outline" className="group/evidence w-fit" />}>
            <ChevronDownIcon data-icon="inline-start" className="transition-transform group-data-panel-open/evidence:rotate-180" />
            Evidence & sources
            <Badge variant="secondary">{facts.length} facts</Badge>
            <Badge variant="secondary">{result.sources_used.length} sources</Badge>
          </CollapsibleTrigger>
          <CollapsibleContent className="flex flex-col gap-5">
            <Separator />
            <div className="grid gap-6 md:grid-cols-2">
              <Column title="Evidence">
                {facts.length ? (
                  <ul className="flex flex-col gap-2 text-sm leading-relaxed">
                    {facts.map((f) => (
                      <li key={f.fact_id} className="flex flex-col gap-0.5">
                        <span>
                          <span className="font-mono text-xs font-bold text-muted-foreground">{f.fact_id}</span> {f.text}
                        </span>
                        {f.sources.length > 0 && <span className="font-mono text-xs text-muted-foreground">{f.sources.join(" · ")}</span>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">No fact cards for this answer.</p>
                )}
              </Column>
              <div className="flex flex-col gap-6">
                <Column title="Sources">
                  {result.sources_used.length ? (
                    <ul className="ml-4 list-disc font-mono text-xs leading-relaxed">
                      {result.sources_used.map((s) => (
                        <li key={s.source}>
                          {s.source} <span className="text-muted-foreground">({s.via.join(", ")})</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground">No source was read.</p>
                  )}
                </Column>
                {result.flags.length > 0 && (
                  <Column title="Flags">
                    <ul className="ml-4 list-disc text-sm leading-relaxed text-destructive">
                      {result.flags.map((f, i) => (
                        <li key={i}>{flagText(f)}</li>
                      ))}
                    </ul>
                  </Column>
                )}
                {limitations.length > 0 && (
                  <Column title="Limitations">
                    <ul className="ml-4 list-disc text-sm leading-relaxed">
                      {limitations.map((text, i) => (
                        <li key={i}>{text}</li>
                      ))}
                    </ul>
                  </Column>
                )}
              </div>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  );
}

function Column({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  );
}
