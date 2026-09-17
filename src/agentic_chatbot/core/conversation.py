"""In-memory conversation/session bookkeeping.

This is intentionally minimal for the MVP: sessions live in process memory
and are lost on restart. For a multi-instance deployment, swap this for a
shared store (e.g. Firestore or Memorystore/Redis) behind the same
interface — the API layer only depends on ``get_or_create`` and ``touch``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Session:
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    turn_count: int = 0

    # State for the "would you like a sample chart?" follow-up: set when a
    # BigQuery query returned rows but no chart could be auto-inferred, so
    # the next turn can be checked for an affirmative reply before falling
    # back to normal intent classification. See agents/orchestrator.py.
    pending_chart_offer: bool = False
    last_query_rows: list[dict[str, object]] | None = None
    last_query_sql: str | None = None


class ConversationManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        new_id = session_id or str(uuid.uuid4())
        session = Session(session_id=new_id)
        self._sessions[new_id] = session
        return session

    def touch(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_active_at = time.time()
            session.turn_count += 1

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
