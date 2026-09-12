from agent_server.history import latest_user_item, normalize_history_items


def test_normalize_idless_assistant_output_text():
    messages = [
        {"role": "user", "content": "What is delayed?"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Inventory is delayed."}],
        },
    ]

    assert normalize_history_items(messages)[1] == {
        "role": "assistant",
        "content": "Inventory is delayed.",
    }


def test_latest_user_item_preserves_current_turn():
    messages = [
        {"role": "user", "content": "Earlier"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Now"},
    ]

    assert latest_user_item(messages) == {"role": "user", "content": "Now"}