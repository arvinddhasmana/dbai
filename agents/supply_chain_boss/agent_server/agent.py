"""MLflow Responses API adapter for the LangGraph Boss Agent."""

import logging
import re
from typing import Any
from uuid import uuid4
from urllib.parse import urlsplit

import mlflow
from mlflow.genai.agent_server import get_request_headers, invoke
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from agent_server.config import Settings, get_settings
from agent_server.workflow import BossRuntime, create_runtime_async, handle_message

_runtime: BossRuntime | None = None

mlflow.langchain.autolog()


def set_runtime(runtime: BossRuntime) -> None:
    global _runtime
    _runtime = runtime


async def close_runtime() -> None:
    global _runtime
    if _runtime is None:
        return
    await _runtime.aclose()
    _runtime = None


def get_runtime() -> BossRuntime:
    if _runtime is None:
        raise RuntimeError("Boss Agent runtime has not been initialized.")
    return _runtime


def _latest_user_message(request: ResponsesAgentRequest) -> str:
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
    if request.context and request.context.conversation_id:
        return str(request.context.conversation_id)
    custom_inputs = dict(request.custom_inputs or {})
    return str(custom_inputs.get("conversation_id") or uuid4())


def _trusted_identity(headers: dict[str, Any]) -> tuple[str | None, str | None]:
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
    normalized = {key.casefold(): str(value) for key, value in headers.items()}
    # Never add this token to LangGraph state, checkpoints, or logs.
    return normalized.get("x-forwarded-access-token") or None


def _app_base_url(headers: dict[str, Any]) -> str | None:
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
    set_runtime(await create_runtime_async(settings or get_settings()))
