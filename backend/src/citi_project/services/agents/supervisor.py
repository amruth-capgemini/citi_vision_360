"""Bounded supervisor -> specialists -> certified tools (+ optional explorers) -> grounded synthesis.

The stages run as a LangGraph graph (``graph.Orchestrator``); this class holds the
bounds and collaborators and keeps the original ``ask`` contract.
"""

from copy import deepcopy

from .contracts import AgentError, ConversationState, SPECIALISTS, SpecialistDeclined, plan_schema, validate
from .graph import Orchestrator
from . import prompts
from .tools import ApprovedTools, CanonicalResolver
from ..structured_data.query_service import NAMESPACE


class SpecialistAgent:
    """One role instance per specialist, each with an independent tool schema."""
    def __init__(self, name, model):
        if name not in SPECIALISTS:
            raise AgentError("Unknown specialist")
        self.name, self.model = name, model

    def plan(self, question, vendor_ids, focus):
        schema = plan_schema(self.name)
        result = validate(self.model.complete(
            self.name, prompts.SPECIALIST,
            {"prompt_version": prompts.PROMPT_VERSION, "specialist": self.name, "question": question,
             "resolved_vendor_ids": vendor_ids, "focus": focus, "allowed_tools": list(SPECIALISTS[self.name])}, schema), schema)
        if result["status"] != "ready" or not result["calls"]:
            raise SpecialistDeclined("Specialist requires clearer scope or scenario parameters")
        return result["calls"]


class SupervisorAgent:
    def __init__(self, decision_service, model, *, explorer=None, max_tool_calls=8, max_service_calls=12,
                 max_question_chars=2000, max_explore_queries=15):
        for value, ceiling in ((max_tool_calls, 8), (max_service_calls, 16), (max_question_chars, 4000), (max_explore_queries, 30)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise AgentError("Invalid agent bound")
        self.model = model
        self.tools = ApprovedTools(decision_service)
        self.resolver = CanonicalResolver(decision_service.structured)
        self.specialists = {name: SpecialistAgent(name, model) for name in SPECIALISTS}
        self.explorer = explorer
        self.max_tool_calls, self.max_service_calls = max_tool_calls, max_service_calls
        self.max_question_chars, self.max_explore_queries = max_question_chars, max_explore_queries
        self.orchestrator = Orchestrator(self)

    @staticmethod
    def _empty():
        return {"namespace": NAMESPACE, "status": "answered", "intent": None, "route": None,
                "resolved_entities": {}, "specialists_used": [], "tool_results": [],
                "facts": [], "calculations": [], "flags": [], "evidence": [], "interpretation": [],
                "assumptions": [], "limitations": [], "final_answer": "", "prompt_version": prompts.PROMPT_VERSION,
                "findings": [], "sources_used": [], "trace": []}

    def _state(self, question, state):
        valid = isinstance(question, str) and question.strip() and len(question) <= self.max_question_chars
        if valid and state is not None and not isinstance(state, ConversationState):
            raise AgentError("Expected caller-owned ConversationState")
        return state if isinstance(state, ConversationState) else ConversationState()

    def ask(self, question, *, state=None):
        state = self._state(question, state)
        return deepcopy(self.orchestrator.invoke(question, state, self._empty()))

    def ask_stream(self, question, *, state=None):
        """Like ``ask``, but yields (kind, payload) events as the graph runs; the last event is ("result", result)."""
        state = self._state(question, state)
        for kind, payload in self.orchestrator.stream(question, state, self._empty()):
            yield kind, deepcopy(payload)
