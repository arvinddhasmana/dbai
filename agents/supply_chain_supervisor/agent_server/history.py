"""Helpers for replaying Responses API history through the Agents SDK."""


def normalize_history_items(messages: list[dict]) -> list[dict]:
    """Convert id-less assistant Responses messages to easy-input messages."""
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


def latest_user_item(messages: list[dict]) -> dict | None:
    """Return the latest user item for a Lakebase-backed turn."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return message
    return None