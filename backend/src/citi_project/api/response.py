"""The chat response contract, and the evidence view the UI's Evidence tab renders.

The evidence view answers "what ran, where, and for which agent": every certified
tool call with its arguments and source datasets, and every SQL or Cypher query the
explorers attempted (kept, empty, rejected or failed) with its tables or graph.
"""

import json

EXPLORATION = ("run_sql", "run_cypher")
CONTRACT = ("status", "final_answer", "facts", "evidence", "flags", "limitations", "specialists_used", "sources_used", "trace")
EXTRA = ("narrative", "route", "intent", "understanding", "resolved_entities", "selected_fact_ids", "assumptions", "calculations",
         "prompt_version")


def _plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _source_label(source):
    """One readable location for an evidence source: schema.table and line, graph relationship, or document and page."""
    if source.get("query_language"):
        where = source.get("source_dataset") or f"{source.get('graph', 'business')} graph"
        return f"{source['query_language'].upper()} {source.get('query_id', '')} on {where}".strip()
    dataset = source.get("source_dataset")
    record, line = source.get("source_record_id"), source.get("source_line")
    if dataset:
        return f"{dataset}{f' line {line}' if line not in (None, '') else ''}{f' ({record})' if record else ''}"
    if source.get("relationship"):
        return f"graph {source.get('source', '?')} -{source['relationship']}-> {source.get('target', '?')}"
    document = source.get("document_id") or source.get("document")
    if document:
        page = source.get("page")
        return f"document {document}{f' page {page}' if page not in (None, '') else ''}"
    subject = source.get("subject") or {}
    if subject:
        return f"graph evidence for {subject.get('class_id')} {subject.get('identity')}"
    return source.get("source_type") or source.get("namespace") or "source"


def _source_name(source):
    """The system object a source lives in, without the record: table, graph relationship type or document."""
    if source.get("query_language"):
        return source.get("source_dataset") or f"{source.get('graph', 'business')} graph"
    if source.get("source_dataset"):
        return source["source_dataset"]
    if source.get("relationship"):
        return f"graph :{source['relationship']}"
    if source.get("document_id") or source.get("document"):
        return f"document {source.get('document_id') or source.get('document')}"
    return f"graph {source['source_type']}" if str(source.get("source_type", "")).startswith("neo4j") else source.get("source_type", "source")


def _facts_by_tool(result):
    out = {}
    for card in result.get("facts", []):
        out.setdefault(card["tool_id"], []).append(card["fact_id"])
    return out


def tool_calls(result):
    """Certified tool calls: which agent called what, with which arguments, reading which sources."""
    facts, timings = _facts_by_tool(result), {}
    for entry in result.get("trace", []):
        if entry.get("node") == "execute.tool":
            timings[entry.get("tool_id")] = entry
    calls = []
    for tool in result.get("tool_results", []):
        if tool["tool"] in EXPLORATION:
            continue
        evidence = tool["result"].get("evidence", [])
        sources = list(dict.fromkeys(_source_name(e) for e in evidence))
        entry = timings.get(tool.get("tool_id"), {})
        calls.append({"tool_id": tool.get("tool_id"), "tool": tool["tool"], "agent": tool.get("specialist"),
                      "arguments": tool.get("arguments", {}), "namespace": tool["result"].get("namespace"),
                      "status": entry.get("status", "ok"), "duration_ms": entry.get("duration_ms"),
                      "sources": sources, "evidence_count": len(evidence), "fact_ids": facts.get(tool.get("tool_id"), [])})
    return calls


def queries(result):
    """Every explorer query attempt in run order, joined to the rows it contributed when it was kept."""
    facts = _facts_by_tool(result)
    kept = {}
    for tool in result.get("tool_results", []):
        if tool["tool"] in EXPLORATION:
            kept.setdefault((tool.get("specialist"), tool["arguments"]["query"][:1000]), tool)
    out = []
    for entry in result.get("trace", []):
        if entry.get("node") != "explore":
            continue
        for step in entry.get("steps", []):
            if step.get("action") not in EXPLORATION:
                continue
            language = "sql" if step["action"] == "run_sql" else "cypher"
            tool = kept.get((entry.get("agent"), (step.get("query") or "")[:1000]))
            item = {"agent": entry.get("agent"), "round": entry.get("round", 0), "query_id": step.get("query_id"),
                    "language": language, "purpose": step.get("purpose"), "thought": step.get("thought"),
                    "status": step.get("status"), "message": step.get("message"), "row_count": step.get("row_count"),
                    "duration_ms": step.get("duration_ms"), "query": step.get("query") or "",
                    "datasets": step.get("datasets") or [], "graph": step.get("graph") if language == "cypher" else None,
                    "kept": tool is not None}
            if tool is not None:
                found = tool["result"]["facts"]
                evidence = (tool["result"].get("evidence") or [{}])[0]
                item.update(tool_id=tool.get("tool_id"), query=tool["arguments"]["query"], columns=found.get("columns", []),
                            rows=found.get("rows", []), truncated=found.get("truncated", False),
                            records=evidence.get("records", []), labels=evidence.get("labels", []),
                            namespace=tool["result"].get("namespace"), fact_ids=facts.get(tool.get("tool_id"), []))
            out.append(item)
    return out


def agents(result):
    """Supervisor and specialists: what each planned, called and explored."""
    rows, order = {}, []

    def row(name):
        if name not in rows:
            order.append(name)
            rows[name] = {"agent": name, "role": "supervisor" if name == "supervisor" else "specialist", "status": "ok",
                          "tools": [], "queries_run": 0, "queries_kept": 0, "duration_ms": 0, "used": False}
        return rows[name]

    for entry in result.get("trace", []):
        name = entry.get("agent")
        if not name:
            continue
        agent = row(name)
        agent["duration_ms"] += entry.get("duration_ms") or 0
        if entry.get("node") == "plan":
            agent["tools"] = list(dict.fromkeys(agent["tools"] + entry.get("tools", [])))
            if entry.get("status") in ("declined", "rejected"):
                agent["status"] = entry["status"]
        elif entry.get("node") == "execute.tool" and entry.get("action") not in agent["tools"]:
            agent["tools"].append(entry["action"])
        elif entry.get("node") == "explore":
            agent["queries_run"] += entry.get("queries", 0)
            agent["queries_kept"] += len(entry.get("kept", []))
    for name in result.get("specialists_used", []):
        row(name)["used"] = True
    if "supervisor" in rows:
        rows["supervisor"]["used"] = True
    return [rows[name] for name in order]


def chat_response(result, *, session_id, question, memory=None):
    body = {"session_id": session_id, "question": question, **{k: result.get(k) for k in CONTRACT + EXTRA}}
    # What the session remembers after this turn: the vendor and contract in focus and recent turns.
    body["memory"] = memory or {"active_vendor_id": None, "active_contract_id": None, "recent_turns": []}
    body["evidence_view"] = {"agents": agents(result), "tool_calls": tool_calls(result), "queries": queries(result)}
    labels = {e["evidence_id"]: _source_label(e["source"]) for e in body["evidence"] or []}
    for card in body["facts"] or []:
        card["sources"] = [labels[e] for e in card.get("evidence_ids", []) if e in labels]
    return _plain(body)
