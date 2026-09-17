from __future__ import annotations

from agentic_chatbot.core.conversation import ConversationManager


def test_get_or_create_generates_new_session_id() -> None:
    manager = ConversationManager()
    session = manager.get_or_create(None)
    assert session.session_id
    assert session.turn_count == 0


def test_get_or_create_reuses_existing_session() -> None:
    manager = ConversationManager()
    first = manager.get_or_create(None)
    second = manager.get_or_create(first.session_id)
    assert first.session_id == second.session_id


def test_touch_increments_turn_count() -> None:
    manager = ConversationManager()
    session = manager.get_or_create(None)
    manager.touch(session.session_id)
    manager.touch(session.session_id)
    assert session.turn_count == 2


def test_clear_removes_session() -> None:
    manager = ConversationManager()
    session = manager.get_or_create(None)
    manager.clear(session.session_id)
    recreated = manager.get_or_create(session.session_id)
    assert recreated.turn_count == 0
