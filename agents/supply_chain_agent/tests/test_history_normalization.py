from agent_server.history import normalize_history_items


def test_normalizes_idless_assistant_output_text():
    messages = [
        {"role": "user", "content": "hello"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hi"}],
        },
    ]

    assert normalize_history_items(messages) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi"},
    ]


def test_normalizes_plain_string_assistant_content():
    messages = [{"type": "message", "role": "assistant", "content": "reply"}]

    assert normalize_history_items(messages) == [{"role": "assistant", "content": "reply"}]


def test_joins_multiple_output_text_parts():
    messages = [{
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "output_text", "text": "part A"},
            {"type": "output_text", "text": "part B"},
        ],
    }]

    assert normalize_history_items(messages) == [{
        "role": "assistant",
        "content": "part A\npart B",
    }]


def test_preserves_assistant_items_with_ids():
    message = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "typed reply"}],
    }

    assert normalize_history_items([message]) == [message]