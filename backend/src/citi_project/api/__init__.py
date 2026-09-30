"""FastAPI backend for the chat UI (Phase 4): read-only, sessions held in memory only."""

from .app import create_app

__all__ = ["create_app"]
