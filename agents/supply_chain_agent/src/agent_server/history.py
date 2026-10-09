"""Normalize replayed Responses API history for the OpenAI Agents SDK.

MLflow may replay assistant outputs with response-specific IDs and content
blocks. The Runner accepts a simpler role/content history, so only compatible
assistant messages are normalized and all other items are preserved unchanged.
"""


def normalize_history_items(messages: list[dict]) -> list[dict]:
    """Convert replayed assistant response messages to Runner role/content items."""
    normalized: list[dict] = []
    for message in messages:
        if message.get("type") != "message" or message.get("role") != "assistant":
            normalized.append(message)
            continue
        content = message.get("content")
        if isinstance(content, str):
            normalized.append({"role": "assistant", "content": content})
        elif isinstance(content, list) and "id" not in message:
            text = "\n".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "output_text"
            )
            normalized.append({"role": "assistant", "content": text})
        else:
            normalized.append(message)
    return normalized