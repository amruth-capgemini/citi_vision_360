"""citi-api: FastAPI backend for the chat UI.

    POST /api/chat            {session_id?, question} -> answer, facts, evidence, flags, limitations, agents, trace
    GET  /api/chat/stream     ?session_id&question    -> Server-Sent Events: node, step, result (or error)
    GET  /api/sources         catalog summary (systems, datasets, domains)
    GET  /api/dashboard       ?refresh: portfolio scan (renewal notice, spend, risk, SLA, review items), cached for a day
    GET  /api/health          Postgres, Neo4j and model configuration, without secrets

Everything is read-only; sessions live in memory with a TTL and a size cap.
"""

import argparse
import json
import logging
import os
import queue
import threading
import time

from ..services.agents.contracts import MAX_HISTORY
from .dashboard import build_dashboard, read_renewal_terms
from .response import chat_response
from .sessions import SessionStore

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
EXAMPLES = [
    "What do we know about Aurelix Codeworks?",
    "Which contracts expire in the next 90 days?",
    "How dependent are we on Veylora Application Services?",
    "What is missing for vendor Brivanta Quality Labs?",
    "What contracts are at major risk?",
    "Why is Lumenquay Identity Systems above budget?",
    "Where can we simplify the vendor footprint?",
    "What if India contractors are reduced by 20%?",
]
log = logging.getLogger("citi_project.api")


class LiveRuntime:
    """The live supervisor and its collaborators, built from the environment on first use."""

    DASHBOARD_TTL_SECONDS = 24 * 60 * 60

    def __init__(self, *, explore=True):
        self.explore = explore
        self._live, self._sources = None, None
        self._dashboard, self._dashboard_at = None, 0.0
        self._lock = threading.Lock()
        self._dashboard_lock = threading.Lock()

    @property
    def live(self):
        with self._lock:
            if self._live is None:
                from ..services.agents.cli import wire_live
                self._live = wire_live(explore=self.explore)
            return self._live

    @property
    def supervisor(self):
        return self.live["supervisor"]

    def sources(self):
        if self._sources is None:
            rows = self.live["catalog"].overview()
            systems, domains = {}, {}
            for row in rows:
                system = systems.setdefault(row["system"], {"system": row["system"], "datasets": 0, "rows": 0})
                system["datasets"] += 1
                system["rows"] += row.get("row_count") or 0
                for domain in row.get("domains") or ():
                    domains.setdefault(domain, set()).add(row["dataset_id"])
            self._sources = {
                "systems": sorted(systems.values(), key=lambda s: s["system"]),
                "datasets": sorted(({"dataset": r["dataset_id"], "system": r["system"], "rows": r.get("row_count"), "grain": r.get("grain"),
                                     "fields": r.get("fields"), "domains": r.get("domains") or [], "contracts": r.get("contracts")}
                                    for r in rows), key=lambda d: d["dataset"]),
                "domains": [{"domain": d, "datasets": sorted(v)} for d, v in sorted(domains.items())]}
        return self._sources

    def dashboard(self, *, refresh=False):
        """The portfolio scan, re-read from PostgreSQL and the graph once a day or on request."""
        with self._dashboard_lock:
            if refresh or self._dashboard is None or time.monotonic() - self._dashboard_at > self.DASHBOARD_TTL_SECONDS:
                from ..services.structured_data import StructuredQueryService
                from ..services.structured_data.query_service import NAMESPACE
                structured = StructuredQueryService.from_postgres()
                self._dashboard = build_dashboard(structured.list_forecast_records(),
                                                  read_renewal_terms(self.live["client"], NAMESPACE),
                                                  as_of=structured.as_of_date)
                self._dashboard_at = time.monotonic()
            return self._dashboard

    def health(self):
        checks = {"postgres": self._check_postgres(), "neo4j": self._check_neo4j(), "model": self._check_model()}
        return {"status": "ok" if all(c["ok"] for c in checks.values()) else "degraded", "checks": checks}

    @staticmethod
    def _check_postgres():
        # Only a category is reported: connection errors can echo the DSN.
        try:
            import psycopg
            from ..services.postgres import PostgresConfig
            with psycopg.connect(PostgresConfig.from_env().reader_dsn, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return {"ok": True, "role": "reader"}
        except Exception as exc:
            return {"ok": False, "detail": type(exc).__name__}

    def _check_neo4j(self):
        try:
            self.live["client"].read(lambda tx: list(tx.run("RETURN 1 AS ok")))
            return {"ok": True, "transport": os.environ.get("NEO4J_TRANSPORT", "bolt")}
        except Exception as exc:
            return {"ok": False, "detail": type(exc).__name__}

    @staticmethod
    def _check_model():
        if os.environ.get("AZURE_OPENAI_ENDPOINT"):
            configured = bool(os.environ.get("AZURE_OPENAI_API_KEY") and (os.environ.get("AZURE_OPENAI_DEPLOYMENT")
                                                                          or os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT")))
            return {"ok": configured, "provider": "azure_openai", "configured": configured}
        configured = bool(os.environ.get("OPENAI_API_KEY"))
        return {"ok": configured, "provider": "openai", "configured": configured}

    def close(self):
        if self._live is not None:
            self._live["close"]()


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def create_app(runtime=None, *, sessions=None, origins=None):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel, Field

    if origins is None:
        configured = os.environ.get("CITI_CORS_ORIGINS")
        origins = DEV_ORIGINS if configured is None else [origin.strip() for origin in configured.split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("CITI_CORS_ORIGINS must contain explicit origins, not '*'")

    runtime = runtime or LiveRuntime()
    sessions = sessions or SessionStore()

    @asynccontextmanager
    async def lifespan(_):
        yield
        runtime.close()

    app = FastAPI(title="Citi vendor decision intelligence", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    app.state.runtime, app.state.sessions = runtime, sessions

    class ChatRequest(BaseModel):
        question: str = Field(min_length=1, max_length=4000)
        session_id: str | None = Field(default=None, max_length=64)

    def session_for(session_id):
        try:
            return sessions.get(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    def failed(question, session):
        return {"session_id": session.session_id, "question": question, "status": "unavailable",
                "final_answer": "The agent could not complete this request; no facts are inferred.",
                "facts": [], "evidence": [], "flags": [], "limitations": [{"code": "api_error", "message": "Internal error while answering."}],
                "specialists_used": [], "sources_used": [], "trace": [],
                "evidence_view": {"agents": [], "tool_calls": [], "queries": []}}

    @app.post("/api/chat")
    def chat(request: ChatRequest):
        session = session_for(request.session_id)
        with session.lock:
            try:
                result = runtime.supervisor.ask(request.question, state=session.state)
            except Exception:
                log.exception("chat failed")
                return failed(request.question, session)
            memory = session.state.context(turns=MAX_HISTORY)
        return chat_response(result, session_id=session.session_id, question=request.question, memory=memory)

    @app.get("/api/chat/stream")
    def chat_stream(question: str = Query(min_length=1, max_length=4000), session_id: str | None = Query(default=None, max_length=64)):
        session = session_for(session_id)
        events = queue.Queue()

        def work():
            # The graph runs on its own thread; a client that disconnects just stops reading.
            with session.lock:
                events.put(("session", {"session_id": session.session_id}))
                try:
                    for kind, payload in runtime.supervisor.ask_stream(question, state=session.state):
                        if kind == "result":
                            payload = chat_response(payload, session_id=session.session_id, question=question,
                                                    memory=session.state.context(turns=MAX_HISTORY))
                        events.put((kind, payload))
                except Exception:
                    log.exception("chat stream failed")
                    events.put(("error", failed(question, session)))
                events.put(None)

        threading.Thread(target=work, daemon=True, name=f"chat-{session.session_id}").start()

        def stream():
            while True:
                try:
                    item = events.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    return
                yield _sse(*item)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/sources")
    def sources():
        try:
            return runtime.sources()
        except Exception:
            log.exception("sources failed")
            raise HTTPException(status_code=503, detail="The metadata catalog is unavailable.") from None

    @app.get("/api/dashboard")
    def dashboard(refresh: bool = False):
        try:
            return runtime.dashboard(refresh=refresh)
        except Exception:
            log.exception("dashboard failed")
            raise HTTPException(status_code=503, detail="The dashboard sources are unavailable.") from None

    @app.get("/api/examples")
    def examples():
        return {"examples": EXAMPLES}

    @app.get("/api/health")
    def health():
        return runtime.health()

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(prog="citi-api", description=__doc__.splitlines()[0])
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-explore", action="store_true", help="certified tools only")
    args = parser.parse_args(argv)
    from ..env import load_env
    load_env()
    host = args.host if args.host is not None else os.environ.get("CITI_API_HOST", "127.0.0.1")
    port = args.port if args.port is not None else int(os.environ.get("PORT", "8000"))
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    import uvicorn
    uvicorn.run(create_app(LiveRuntime(explore=not args.no_explore)), host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
