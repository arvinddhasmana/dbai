"""Conversation history normalization for OpenAI Agents SDK requests."""


def normalize_history_items(messages: list[dict]) -> list[dict]:
    """Normalize replayed assistant messages before passing them to Runner."""
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