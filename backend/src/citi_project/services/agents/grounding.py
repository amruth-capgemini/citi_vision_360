"""Source-backed answer cards. The model can select text, never author facts."""

from copy import deepcopy
from decimal import Decimal
import json
import re


def _value(value):
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


EXPLORATION_ROWS = 5


def _money(value):
    """Display form of a source amount (thousands separators, cents); the value itself is unchanged."""
    if value is None:
        return "unavailable"
    try:
        return f"{Decimal(str(value)):,.2f}"
    except ArithmeticError:
        return str(value)


def _cell(value, limit=80):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def build_grounding(tool_results):
    cards, evidence, assumptions, limitations, calculations, flags = [], [], [], [], [], []
    evidence_keys = {}
    for index, tool in enumerate(tool_results, 1):
        before = len(cards)
        tid = f"T{index}"
        result = tool["result"]
        local_evidence = []
        for source in result["evidence"]:
            key = json.dumps(source, sort_keys=True)
            if key not in evidence_keys:
                eid = f"E{len(evidence) + 1}"
                evidence_keys[key] = eid
                evidence.append({"evidence_id": eid, "source": deepcopy(source)})
            local_evidence.append((evidence_keys[key], source))
        for dest, name in ((assumptions, "assumptions"), (limitations, "limitations"), (calculations, "calculations"), (flags, "flags")):
            for item in result.get(name, []):
                wrapped = {"tool_id": tid, "value": deepcopy(item)} if name in ("calculations", "flags") else deepcopy(item)
                if wrapped not in dest:
                    dest.append(wrapped)

        def add(text, paths, topic, kind="fact", sources=None):
            if sources is not None:
                refs = list(dict.fromkeys(eid for eid, source in local_evidence if source in sources))
                cards.append({"fact_id": f"F{len(cards) + 1}", "tool_id": tid, "topic": topic, "kind": kind, "text": text,
                              "source_paths": paths, "evidence_ids": refs[:3]})
                return

            def relevant(source):
                dataset = source.get("source_dataset", "")
                subject = source.get("subject", {})
                cls = subject.get("class_id", "")
                relation = source.get("relationship") or subject.get("relationship")
                if topic == "spend":
                    return dataset == "CT_Technology_Financials"
                if topic == "workforce" or topic == "scenario":
                    return dataset == "CT_Workforce_Organization"
                if topic == "risk":
                    return cls in ("RiskAssessment", "RiskIssue") or relation in ("ASSESSES", "RAISED_IN", "AFFECTS")
                if topic == "sla":
                    return cls in ("PerformanceMeasurement", "ServiceLevelAgreement") or relation in ("HAS_SLA", "MEASURED_AGAINST")
                if topic == "dependencies":
                    return relation in ("SUPPORTS", "USES_PORTAL", "FUNDS") or dataset == "contract_application_bridge"
                return dataset in ("canonical_vendor_master", "contract_application_bridge")
            refs = [eid for eid, source in local_evidence if relevant(source)]
            vendors = re.findall(r"\bV-(\d{3})\b", text)
            if vendors:
                refs = [eid for eid, source in local_evidence if relevant(source) and any(
                    re.search(r"-" + vendor + r"(?:[-_\" ]|$)", json.dumps(source)) for vendor in vendors)]
            cards.append({"fact_id": f"F{len(cards) + 1}", "tool_id": tid, "topic": topic,
                          "kind": kind, "text": text, "source_paths": paths, "evidence_ids": refs[:3]})

        def context(f, scope, prefix="/facts"):
            vendor = scope.get("vendor_id") or f.get("identity", {}).get("Vendor_ID", "Vendor")
            identity = f.get("identity")
            if identity:
                add(f"{vendor} is {identity['Vendor_Name']}, contract {identity['Contract_ID']}, organization {identity['Organization_ID']} and product {identity['Product_ID']}.", [prefix + "/identity"], "overview")
            commercial = f.get("commercial")
            if commercial:
                add(f"{vendor}: contract expires {commercial['contract']['Contract_End_Date']} ({commercial['days_to_expiry']} days from {scope.get('as_of_date')}); service {commercial['service_id']}, SOW {commercial['sow_id']}.", [prefix + "/commercial"], "renewal")
            if "financial" in f:
                finance(f["financial"], vendor, prefix + "/financial")
            if "workforce" in f:
                add(f"{vendor}: {_value(f['workforce'].get('representative_workforce_count'))} representative workforce assignments; this is not enterprise headcount.", [prefix + "/workforce/representative_workforce_count"], "workforce")
            if "dependencies" in f:
                d = f["dependencies"]
                apps = ", ".join(sorted({a['application_id'] for a in d['applications']})) or "none returned"
                add(f"{vendor}: {_value(d['supported_application_count'])} supported applications, {_value(d['used_portal_count'])} used portals, {_value(d['service_count'])} services. Application IDs: {apps}. Counts complete: {_value(d['complete'])}.", [prefix + "/dependencies"], "dependencies")
            if "risk" in f:
                r = f["risk"]
                entries = []
                for assessment in r["assessments"]:
                    status = assessment.get("status")
                    entry = f"{assessment['assessment_id']}: {_value(status)}"
                    if status != "Missing":
                        entry += f", assessment date {_value(assessment.get('assessment_date'))}, {'last-known ' if status != 'Current' else ''}tier {_value(assessment.get('risk_tier'))}"
                    else:
                        entry += ", assessment evidence unavailable; no risk tier inferred"
                    entries.append(entry)
                add(f"{vendor}: risk status {_value(r['status'])}. {'; '.join(entries) or 'No assessment returned'}. Open issues: {_value(r['open_issue_count'])} (returned source status Open).", [prefix + "/risk"], "risk")
            if "sla" in f:
                s = f["sla"]
                breaches = [m for m in s["measurements"] if m["breach"] is True]
                detail = "; ".join(f"{m['period']}: actual {m['actual']} vs target {m['target']} {m['unit']}" for m in breaches)
                add(f"{vendor}: SLA breach {_value(s['has_breach'])}; {_value(s['breached_measurement_count'])} breached returned measurements. {detail}", [prefix + "/sla"], "sla")

        def finance(f, vendor, prefix):
            add(f"{vendor}: full-year {f['year']} Budget USD {_money(f['budget'])}; Forecast USD {_money(f['forecast'])}; forecast minus budget USD {_money(f['forecast_variance_amount'])} ({_value(f['forecast_variance_pct'])}%).", [prefix], "spend", "calculation")
            add(f"{vendor}: January-August Actual USD {_money(f['actual_ytd'])} vs comparable YTD Budget USD {_money(f['comparable_ytd_budget'])}; variance USD {_money(f['actual_ytd_variance_amount'])} ({_value(f['actual_ytd_variance_pct'])}%).", [prefix], "spend", "calculation")

        f = result["facts"]
        if not f:
            add("No facts were returned for this scope.", ["/facts"], "overview")
        elif result["capability"] == "renewal" and "contracts" in f:
            for n, c in enumerate(f["contracts"]):
                context(c["facts"], c["scope"], f"/facts/contracts/{n}/facts")
        elif result["capability"] in ("vendor_360", "renewal", "risk"):
            context(f, result["scope"])
        elif result["capability"] == "spend_forecast":
            finance(f, result["scope"]["vendor_id"], "/facts")
            v = f["actual_vs_forecast_ytd"]
            add(f"Actual minus comparable YTD Forecast: USD {_money(v['amount'])} ({_value(v['percent'])}%). This comparison does not establish a numerical cause.", ["/facts/actual_vs_forecast_ytd"], "spend", "calculation")
        elif result["capability"] == "rationalization":
            add(f"{f['candidate_count']} potential overlap candidate pairs in the requested scope. These are not consolidation recommendations.", ["/facts/candidate_count"], "rationalization")
            for n, candidate in enumerate(f["candidates"][:3]):
                overlap = candidate["overlap"]
                add(f"{candidate['vendor_a']} / {candidate['vendor_b']}: same financial product {_value(overlap['product'])}; same organization {_value(overlap['organization'])}; shared applications {', '.join(overlap['shared_applications']) or 'none'}. Shared allocation does not establish substitutability.", [f"/facts/candidates/{n}"], "rationalization")
            if len(f["candidates"]) > 3:
                limitations.append({"code": "answer_examples", "message": "Only the first three overlap examples are offered for synthesis; the complete candidate list remains in tool_results."})
        elif result["capability"] == "exploration":
            # Rows from a guarded dynamic query, rendered verbatim by the host.
            rows, sources = f["rows"], result["evidence"]
            more = "+" if f["truncated"] else ""
            for n, row in enumerate(rows[:EXPLORATION_ROWS]):
                cells = "; ".join(f"{column}={_cell(value)}" for column, value in zip(f["columns"], row)
                                  if value not in (None, "") and column != "_source_file")
                own = [sources[n]] if len(sources) == len(rows) else sources[:1]
                add(f"Source rows {f['query_id']} ({f['source']}), row {n + 1} of {f['row_count']}{more}: {cells}.",
                    [f"/facts/rows/{n}"], "exploration", sources=own)
            if len(rows) > EXPLORATION_ROWS:
                limitations.append({"code": "exploration_rows", "message": f"Only the first {EXPLORATION_ROWS} rows of each dynamic query are offered for synthesis; all returned rows remain in tool_results."})
        elif result["capability"] == "what_if":
            selected = f["selected_baseline"]
            groups = "; ".join(f"{g['country']} {g['worker_type']}: {g['representative_assignment_count']}" for g in selected["groups"])
            add(f"Selected baseline: {selected['representative_assignment_count']} representative assignments. {groups}", ["/facts/selected_baseline"], "workforce")
            calculation = result["calculations"][0]
            add(f"Scenario affects {f['affected_assignment_count']} assignments; selected cohort becomes {f['selected_resulting_counts']['representative_assignment_count']}; scoped portfolio becomes {f['resulting_counts']['representative_assignment_count']}. Requested percentage {calculation['requested_percentage']}%; effective percentage {_value(calculation['effective_percentage'])}%. Financial impact: {f['financial_impact']}.", ["/facts", "/calculations/0"], "scenario", "calculation")
            assumptions_with_count = [a for a in result["assumptions"] if isinstance(a, dict) and "requested_assignment_count" in a]
            if assumptions_with_count:
                add(f"Requested assignment count: {assumptions_with_count[0]['requested_assignment_count']}; selected IDs: {', '.join(f['affected_assignment_ids']) or 'none'}. The 100% applies only to this selected cohort.", ["/facts/affected_assignment_ids", "/assumptions"], "scenario")
        if len(cards) == before:
            add("No matching facts were returned for the requested scope.", ["/facts"], "overview")
    return {"facts": cards, "calculations": calculations, "flags": flags, "evidence": evidence,
            "assumptions": assumptions, "limitations": limitations}


def model_context(grounding):
    refs = {eid for c in grounding["facts"] for eid in c["evidence_ids"]}
    sources = []
    for e in grounding["evidence"]:
        if e["evidence_id"] not in refs:
            continue
        source = e["source"]
        projection = {k: source[k] for k in ("source_type", "source_dataset", "source_record_id", "namespace", "document_id", "evidence_ref", "page", "relationship", "source", "target", "subject") if k in source}
        if "subject" in projection:
            projection["subject"] = {k: projection["subject"][k] for k in ("class_id", "identity", "relationship", "source", "target") if k in projection["subject"]}
        if "citations" in source:
            projection["citations"] = [{k: c[k] for k in ("document", "evidence_ref", "page", "source_ref", "review_state", "verification_state") if k in c} for c in source["citations"]["items"][:2]]
        sources.append({"evidence_id": e["evidence_id"], "source": projection})
    return {**{k: grounding[k] for k in ("facts", "calculations", "flags", "assumptions", "limitations")}, "evidence": sources,
            "context_projection": "Up to three evidence references per fact card and two compact stored citations per source are sent to the model. Full tool evidence is retained in the response."}


_ID = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b")
_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
_CITATION = re.compile(r"\[?\b[FET]\d+\b\]?")
_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december"]
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_LONG_DATE = re.compile(rf"\b(?:(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH}\.?,?\s+(\d{{4}})|{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}}))\b", re.I)
_RECOMMENDATION = re.compile(r"\b(?:we|i)\s+(?:recommend|suggest|advise)\b|\brecommend(?:s|ed|ation)?\b|"
                             r"\bshould\s+(?:be\s+)?(?:renew|terminat|consolidat|replac|exit|cut|reduc|extend|switch|cancel)\w*", re.I)


def _month(name):
    return next(i for i, m in enumerate(_MONTH_NAMES, 1) if m.startswith(name.lower()[:3]))


def _values(texts):
    """IDs, ISO dates and numbers present in texts; a date also contributes its year, month and day."""
    ids, dates, numbers = set(), set(), set()
    for text in texts:
        ids |= set(_ID.findall(text))
        for d in _ISO.findall(text):
            dates.add(d)
            numbers.update(Decimal(int(part)) for part in d.split("-"))
        for n in _NUMBER.findall(_ISO.sub(" ", _ID.sub(" ", text))):
            value = Decimal(n.replace(",", ""))
            numbers.update({value, abs(value)})
    return ids, dates, numbers


def verify_narrative(narrative, cards, question):
    """Problems with a model-written answer: any value not in the cards it cites, or a recommendation.

    Every number, amount, percentage, date and ID in a paragraph must appear in that paragraph's
    cited cards (or the question). The summary may use values from any card the answer cites.
    """
    problems = []
    parts = [(p["heading"] + ". " + p["text"], p["fact_ids"], f"paragraph {n}") for n, p in enumerate(narrative["paragraphs"], 1)]
    # The summary draws the answer together, so it may use any card's values.
    parts.append((narrative["summary"], list(cards), "summary"))
    asked = _values([question])
    per_card = {f: _values([c["text"]]) for f, c in cards.items()}
    any_ids = set().union(*(v[0] for v in per_card.values())) if per_card else set()

    def holders(kind, value):
        return [f for f, v in per_card.items() if value in v[kind]]

    def report(where, label, shown, kind, key, fact_ids):
        found = holders(kind, key)
        hint = f"it is in {', '.join(found[:3])}; add that card to fact_ids" if found else "it is in no card; remove it"
        problems.append(f"{where}: {label} {shown} is not in its cited facts {fact_ids}; {hint}")

    for text, fact_ids, where in parts:
        ids, dates, numbers = (a | b for a, b in zip(_values([cards[f]["text"] for f in fact_ids]), asked))
        clean = _CITATION.sub(" ", text)
        for match in _LONG_DATE.finditer(clean):
            day, month, year = (match.group(1), match.group(2), match.group(3)) if match.group(1) else (match.group(5), match.group(4), match.group(6))
            iso = f"{int(year):04d}-{_month(month):02d}-{int(day):02d}"
            if iso not in dates:
                report(where, "date", iso, 1, iso, fact_ids)
        clean = _LONG_DATE.sub(" ", clean)
        for d in _ISO.findall(clean):
            if d not in dates:
                report(where, "date", d, 1, d, fact_ids)
        # An identifier only has to exist in some card: the risk being prevented is an invented ID.
        for i in _ID.findall(clean):
            if i not in ids | any_ids | asked[0]:
                problems.append(f"{where}: identifier {i} is in no card; remove it")
        for n in _NUMBER.findall(_ISO.sub(" ", _ID.sub(" ", clean))):
            value = Decimal(n.replace(",", ""))
            if value not in numbers:
                report(where, "value", n, 2, value, fact_ids) if holders(2, value) else problems.append(
                    f"{where}: value {n} is in no card; copy values exactly and do not round or compute")
        if _RECOMMENDATION.search(text):
            problems.append(f"{where}: remove the recommendation; this answer gives decision context only")
    return list(dict.fromkeys(problems))[:12]


# Limitations about how the answer was assembled; kept in the result, not shown to the reader.
INTERNAL_NOTES = frozenset({"exploration_rows", "answer_examples"})


def _notes(grounding):
    notes = ["Decision context only; no final renewal, consolidation or staffing recommendation."]
    if grounding["flags"]:
        notes.append("Deterministic flags: " + "; ".join(f"{f['value'].get('vendor_id', '')} {f['value']['code']}".strip()
                                                        for f in grounding["flags"]) + ".")
    notes += [a for a in grounding["assumptions"] if isinstance(a, str)]
    notes += [n["message"] for n in grounding["limitations"] if n.get("code") not in INTERNAL_NOTES]
    return list(dict.fromkeys(notes))


def _card_line(card):
    refs = ", ".join([card["fact_id"], *card["evidence_ids"][:3]])
    return f"- {card['text']} [{refs}]"


def render_narrative(narrative, grounding, appended_ids):
    """The model's answer, cited facts appended where it omitted required ones, then host notes."""
    by_id = {c["fact_id"]: c for c in grounding["facts"]}
    lines = [re.sub(r"\s*\[(?:[FET]\d+(?:,\s*)?)+\]", "", narrative["summary"]).strip(), ""]
    for p in narrative["paragraphs"]:
        refs = ", ".join(dict.fromkeys(p["fact_ids"]))
        # Citations are the host's: any the model wrote are removed and the checked ones added once.
        text = re.sub(r"\s*\[(?:[FET]\d+(?:,\s*)?)+\]", "", p["text"]).strip()
        lines += [p["heading"].strip(), f"{text} [{refs}]", ""]
    if appended_ids:
        lines += ["Key facts:", *(_card_line(by_id[i]) for i in appended_ids), ""]
    lines += ["Notes:", *(f"- {n}" for n in _notes(grounding))]
    return "\n".join(lines)


def render(selected_ids, grounding):
    """Fallback when no valid model answer is available: the source-backed statements themselves."""
    by_id = {c["fact_id"]: c for c in grounding["facts"]}
    lines = ["Key facts:", *(_card_line(by_id[i]) for i in dict.fromkeys(selected_ids)), ""]
    lines += ["Notes:", *(f"- {n}" for n in _notes(grounding))]
    return "\n".join(lines)
