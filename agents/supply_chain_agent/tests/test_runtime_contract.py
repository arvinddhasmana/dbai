from types import SimpleNamespace

from agent_server.utils import get_session_id


def test_session_id_uses_conversation_context():
    request = SimpleNamespace(
        context=SimpleNamespace(conversation_id="conversation-123"),
        custom_inputs=None,
    )

    assert get_session_id(request) == "conversation-123"


def test_session_id_falls_back_to_custom_inputs():
    request = SimpleNamespace(
        context=None,
        custom_inputs={"session_id": "session-456"},
    )

    assert get_session_id(request) == "session-456"