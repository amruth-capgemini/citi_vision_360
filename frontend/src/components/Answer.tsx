import type { ChatResponse } from "../api";
import { flagText, noteText } from "../api";
import { AgentPill } from "./common";

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

  return (
    <div className="answer">
      {narrative ? (
        <>
          <p className="summary">{stripCitations(narrative.summary)}</p>
          {narrative.paragraphs.map((p, i) => (
            <div className="paragraph" key={i}>
              <h4>{p.heading}</h4>
              <p>
                {stripCitations(p.text)}{" "}
                {p.fact_ids.map((id) => (
                  <button key={id} className="cite" onClick={() => onFact(id)} title={facts.get(id)?.text}>
                    {id}
                  </button>
                ))}
              </p>
            </div>
          ))}
          {keyFacts.length > 0 && (
            <div className="paragraph">
              <h4>Key facts</h4>
              <ul className="keyfacts">
                {keyFacts.map((id) => (
                  <li key={id}>
                    {facts.get(id)!.text}{" "}
                    <button className="cite" onClick={() => onFact(id)}>
                      {id}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : (
        <p className="plain">{result.status === "answered" ? stripNotes(result.final_answer) : result.final_answer}</p>
      )}

      {result.specialists_used.length > 0 && (
        <div className="used">
          <span>Agents used</span>
          <AgentPill agent="supervisor" />
          {result.specialists_used.map((a) => (
            <AgentPill key={a} agent={a} />
          ))}
        </div>
      )}

      {result.flags.length > 0 && (
        <div className="callout flags">
          <strong>Flags</strong>
          <ul>
            {result.flags.map((f, i) => (
              <li key={i}>{flagText(f)}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="callout limits">
        <strong>Limitations</strong>
        <ul>
          <li>Decision context only; no final renewal, consolidation or staffing recommendation.</li>
          {[...new Set(result.limitations.filter((n) => typeof n === "string" || !INTERNAL.has(n.code ?? "")).map(noteText))].map((text, i) => (
            <li key={i}>{text}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// Notes about how the answer was assembled (grounding.INTERNAL_NOTES); the Evidence tab still lists them.
const INTERNAL = new Set(["exploration_rows", "answer_examples"]);

// Citations are the host's: any the model wrote into its text are replaced by the checked chips.
function stripCitations(text: string) {
  return text.replace(/\s*\[(?:[FET]\d+(?:,\s*)?)+\]/g, "").trim();
}

// The host-rendered answer appends limitations as text; they are shown in their own callout instead.
function stripNotes(text: string) {
  const cut = text.search(/\nNotes:/);
  return cut > 0 ? text.slice(0, cut) : text;
}
