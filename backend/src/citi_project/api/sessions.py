"""In-memory chat sessions: session_id -> ConversationState, with a TTL and a size cap.

Nothing is persisted; a restart forgets every session. A per-session lock keeps a
follow-up from racing the question it follows.
"""

import re
import threading
import time
import uuid

from ..services.agents import ConversationState

SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Session:
    def __init__(self, session_id):
        self.session_id = session_id
        self.state = ConversationState()
        self.lock = threading.Lock()
        self.touched = time.monotonic()


class SessionStore:
    def __init__(self, *, ttl_seconds=1800, max_sessions=500, clock=time.monotonic):
        if ttl_seconds <= 0 or max_sessions <= 0:
            raise ValueError("Session TTL and cap must be positive")
        self.ttl, self.cap, self.clock = ttl_seconds, max_sessions, clock
        self._sessions = {}
        self._lock = threading.Lock()

    def __len__(self):
        return len(self._sessions)

    def get(self, session_id=None):
        """The live session for this id (a new one when unknown, expired or absent)."""
        if session_id is not None and not SESSION_ID.fullmatch(session_id):
            raise ValueError("session_id must be 1-64 letters, digits, '-' or '_'")
        now = self.clock()
        with self._lock:
            for key in [k for k, s in self._sessions.items() if now - s.touched > self.ttl]:
                del self._sessions[key]
            session = self._sessions.get(session_id) if session_id else None
            if session is None:
                session = Session(session_id or uuid.uuid4().hex)
                session.touched = now
                self._sessions[session.session_id] = session
                while len(self._sessions) > self.cap:
                    oldest = min(self._sessions.values(), key=lambda s: s.touched)
                    del self._sessions[oldest.session_id]
            session.touched = now
            return session
