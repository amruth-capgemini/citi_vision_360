import { CircleAlertIcon, InfoIcon } from "lucide-react";
import type { ChatResponse } from "../api";
import { flagText, noteText } from "../api";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { badgeVariants } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { AgentBadge } from "./common";

interface Props {
  result: ChatResponse;
  onFact: (factId: string) => void;
}

/** The answer: the model's narrative with fact citations, or the host-rendered text; flags and limitations always shown. */
export function Answer({ result, onFact }: Props) {
  const narrative = result.status === "answered" ? result.narrative : null;
  const cited = new Set(narrative?.paragraphs.flatMap((p) => p.fact_ids) ?? []);
  const facts = new Map(result.facts.map((f) => [f.fact_id, f]));
  const keyFacts = narrative ? (result.selected_fact_ids ?? []).filter((id) => !cited.has(id) && facts.has(id)) : [];
  const cite = (id: string) => <Cite key={id} id={id} text={facts.get(id)?.text} onClick={() => onFact(id)} />;

  return (
    <div className="flex flex-col gap-3">
      {narrative ? (
        <>
          <p className="text-[15px] leading-relaxed font-medium">{stripCitations(narrative.summary)}</p>
          {narrative.paragraphs.map((p, i) => (
            <div key={i} className="flex flex-col gap-1">
              <h4 className="font-heading text-sm font-semibold">{p.heading}</h4>
              <p className="leading-relaxed">
                {stripCitations(p.text)} {p.fact_ids.map(cite)}
              </p>
            </div>
          ))}
          {keyFacts.length > 0 && (
            <div className="flex flex-col gap-1">
              <h4 className="font-heading text-sm font-semibold">Key facts</h4>
              <ul className="ml-4 list-disc leading-relaxed">
                {keyFacts.map((id) => (
                  <li key={id}>
                    {facts.get(id)!.text} {cite(id)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : (
        <p className="leading-relaxed whitespace-pre-wrap">{result.status === "answered" ? stripNotes(result.final_answer) : result.final_answer}</p>
      )}

      {result.specialists_used.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span>Agents used</span>
          <AgentBadge agent="supervisor" />
          {result.specialists_used.map((a) => (
            <AgentBadge key={a} agent={a} />
          ))}
        </div>
      )}

      {result.flags.length > 0 && (
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>Flags</AlertTitle>
          <AlertDescription>
            <ul className="ml-4 list-disc">
              {result.flags.map((f, i) => (
                <li key={i}>{flagText(f)}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      <Alert>
        <InfoIcon />
        <AlertTitle>Limitations</AlertTitle>
        <AlertDescription>
          <ul className="ml-4 list-disc">
            <li>Decision context only; no final renewal, consolidation or staffing recommendation.</li>
            {[...new Set(result.limitations.filter((n) => typeof n === "string" || !INTERNAL.has(n.code ?? "")).map(noteText))].map((text, i) => (
              <li key={i}>{text}</li>
            ))}
          </ul>
        </AlertDescription>
      </Alert>
    </div>
  );
}

/** A fact citation chip: hover shows the fact, click opens it in the Evidence tab. */
function Cite({ id, text, onClick }: { id: string; text?: string; onClick: () => void }) {
  const chip = (
    <button type="button" className={badgeVariants({ variant: "outline", className: "mx-0.5 h-4 cursor-pointer px-1.5 font-mono text-[10.5px] hover:bg-muted" })} onClick={onClick}>
      {id}
    </button>
  );
  if (!text) return chip;
  return (
    <Tooltip>
      <TooltipTrigger render={chip} />
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}

// Notes about how the answer was assembled (grounding.INTERNAL_NOTES); the Evidence tab still lists them.
const INTERNAL = new Set(["exploration_rows", "answer_examples"]);

// Citations are the host's: any the model wrote into its text are replaced by the checked chips.
export function stripCitations(text: string) {
  return text.replace(/\s*\[(?:[FET]\d+(?:,\s*)?)+\]/g, "").trim();
}

// The host-rendered answer appends limitations as text; they are shown in their own callout instead.
export function stripNotes(text: string) {
  const cut = text.search(/\nNotes:/);
  return cut > 0 ? text.slice(0, cut) : text;
}
