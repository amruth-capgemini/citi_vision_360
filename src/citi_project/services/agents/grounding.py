"""Source-backed answer cards. The model can select text, never author facts."""

from copy import deepcopy
import json
import re


def _value(value):
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


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

        def add(text, paths, topic, kind="fact"):
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
            add(f"{vendor}: full-year {f['year']} Budget USD {_value(f['budget'])}; Forecast USD {_value(f['forecast'])}; forecast minus budget USD {_value(f['forecast_variance_amount'])} ({_value(f['forecast_variance_pct'])}%).", [prefix], "spend", "calculation")
            add(f"{vendor}: January-August Actual USD {_value(f['actual_ytd'])} vs comparable YTD Budget USD {_value(f['comparable_ytd_budget'])}; variance USD {_value(f['actual_ytd_variance_amount'])} ({_value(f['actual_ytd_variance_pct'])}%).", [prefix], "spend", "calculation")

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
            add(f"Actual minus comparable YTD Forecast: USD {_value(v['amount'])} ({_value(v['percent'])}%). This comparison does not establish a numerical cause.", ["/facts/actual_vs_forecast_ytd"], "spend", "calculation")
        elif result["capability"] == "rationalization":
            add(f"{f['candidate_count']} potential overlap candidate pairs in the requested scope. These are not consolidation recommendations.", ["/facts/candidate_count"], "rationalization")
            for n, candidate in enumerate(f["candidates"][:3]):
                overlap = candidate["overlap"]
                add(f"{candidate['vendor_a']} / {candidate['vendor_b']}: same financial product {_value(overlap['product'])}; same organization {_value(overlap['organization'])}; shared applications {', '.join(overlap['shared_applications']) or 'none'}. Shared allocation does not establish substitutability.", [f"/facts/candidates/{n}"], "rationalization")
            if len(f["candidates"]) > 3:
                limitations.append({"code": "answer_examples", "message": "Only the first three overlap examples are offered for synthesis; the complete candidate list remains in tool_results."})
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


def render(selected_ids, grounding):
    by_id = {c["fact_id"]: c for c in grounding["facts"]}
    lines = []
    for identity in dict.fromkeys(selected_ids):
        card = by_id[identity]
        refs = ", ".join([identity, *card["evidence_ids"][:3]])
        label = "Calculation" if card["kind"] == "calculation" else "Finding"
        lines.append(label + ": " + card["text"] + f" [{refs}]")
    if grounding["flags"]:
        lines.append("Deterministic flags: " + "; ".join(f"{f['value'].get('vendor_id', '')} {f['value']['code']}" for f in grounding["flags"]) + ".")
    lines.append("Interpretation: decision context only; no final renewal, consolidation or staffing recommendation.")
    if grounding["assumptions"]:
        lines.append("Assumptions: " + " ".join(a if isinstance(a, str) else json.dumps(a, sort_keys=True) for a in grounding["assumptions"]))
    if grounding["limitations"]:
        lines.append("Limitations: " + " ".join(dict.fromkeys(n["message"] for n in grounding["limitations"])))
    return "\n".join(lines)
