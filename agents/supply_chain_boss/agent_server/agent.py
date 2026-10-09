"""Bridge MLflow's Responses API to the long-lived LangGraph Boss runtime.

MLflow autologging is enabled before importing the graph module so graph work
is traced. Request metadata is taken only from Databricks App ingress headers;
the forwarded user token is passed separately and is never serialized into
workflow state.
"""

import logging
import re
from typing import Any
from uuid import uuid4
from urllib.parse import urlsplit

import mlflow
from mlflow.genai.agent_server import get_request_headers, invoke
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from agent_server.config import Settings, get_settings

mlflow.langchain.autolog()

from agent_server.workflow import (  # noqa: E402
    BossRuntime,
    create_runtime_async,
    handle_message,
)

_runtime: BossRuntime | None = None


def set_runtime(runtime: BossRuntime) -> None:
    """Install the startup-created runtime for request handlers."""
    global _runtime
    _runtime = runtime


async def close_runtime() -> None:
    """Close pooled runtime resources during graceful App shutdown."""
    global _runtime
    if _runtime is None:
        return
    await _runtime.aclose()
    _runtime = None


def get_runtime() -> BossRuntime:
    """Return the initialized runtime or fail explicitly before startup."""
    if _runtime is None:
        raise RuntimeError("Boss Agent runtime has not been initialized.")
    return _runtime


def _latest_user_message(request: ResponsesAgentRequest) -> str:
    """Extract the latest textual user turn from Responses API input items."""
    for item in reversed(request.input):
        item_dict = item.model_dump(exclude_none=True)
        if item_dict.get("role") != "user":
            continue
        content = item_dict.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(
                str(part.get("text", "")).strip()
                for part in content
                if isinstance(part, dict) and part.get("text")
            ).strip()
    raise ValueError("Responses API request must include a non-empty user message.")


def _conversation_id(request: ResponsesAgentRequest) -> str:
    """Prefer the platform conversation ID and generate one for API callers."""
    if request.context and request.context.conversation_id:
        return str(request.context.conversation_id)
    custom_inputs = dict(request.custom_inputs or {})
    return str(custom_inputs.get("conversation_id") or uuid4())


def _trusted_identity(headers: dict[str, Any]) -> tuple[str | None, str | None]:
    """Normalize ingress identity headers for per-user conversation partitioning.

    Email-shaped validation avoids treating arbitrary forwarded header values
    as identity. These headers are trusted only behind Databricks App ingress.
    """
    normalized = {key.casefold(): str(value) for key, value in headers.items()}
    # These values are trusted only when supplied by the Databricks App ingress.
    email = normalized.get("x-forwarded-email", "").strip() or None
    forwarded_user = normalized.get("x-forwarded-user", "").strip() or None
    if email and not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", email):
        email = None
    if email is None and forwarded_user and re.fullmatch(
        r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", forwarded_user
    ):
        email = forwarded_user
    return email, email or forwarded_user


def _user_access_token(headers: dict[str, Any]) -> str | None:
    """Read the user token for request-scoped calls without persisting it."""
    normalized = {key.casefold(): str(value) for key, value in headers.items()}
    # Never add this token to LangGraph state, checkpoints, or logs.
    return normalized.get("x-forwarded-access-token") or None


def _app_base_url(headers: dict[str, Any]) -> str | None:
    """Build an HTTPS App origin from ingress headers for approval deep links.

    Reject URL-shaped hosts and non-HTTPS schemes so caller-controlled values
    cannot turn a Jira description into a link to an arbitrary origin.
    """
    normalized = {key.casefold(): str(value) for key, value in headers.items()}
    host = normalized.get("x-forwarded-host") or normalized.get("host")
    protocol = normalized.get("x-forwarded-proto", "https")
    if not host or "://" in host or "\n" in host:
        return None
    candidate = f"{protocol}://{host}"
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return candidate.rstrip("/")


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    """Run one Boss turn and serialize text plus structured action metadata."""
    runtime = get_runtime()
    headers = dict(get_request_headers())
    _, user_id = _trusted_identity(headers)
    outcome = await handle_message(
        runtime=runtime,
        conversation_id=_conversation_id(request),
        message=_latest_user_message(request),
        user_id=user_id,
        app_base_url=_app_base_url(headers),
        user_access_token=_user_access_token(headers),
    )
    return ResponsesAgentResponse(
        output=[
            {
                "type": "message",
                "id": f"msg_{uuid4().hex}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": outcome["response"]}],
            }
        ],
        custom_outputs={
            key: value
            for key, value in outcome.items()
            if key != "response"
        },
    )


async def initialize(settings: Settings | None = None) -> None:
    """Create and install the graph runtime once during App startup."""
    set_runtime(await create_runtime_async(settings or get_settings()))
