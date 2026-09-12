"""Authentication, MCP, and Responses streaming helpers."""

import logging
import os
from typing import AsyncGenerator, AsyncIterator
from uuid import uuid4

from agents.result import StreamEvent
from databricks.sdk import WorkspaceClient
from mlflow.genai.agent_server import get_request_headers
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentStreamEvent


def get_databricks_host(workspace_client: WorkspaceClient | None = None) -> str:
    client = workspace_client or WorkspaceClient()
    host = client.config.host
    if not host:
        raise RuntimeError("Databricks workspace host is not configured.")
    return host.rstrip("/")


def build_mcp_url(path: str, workspace_client: WorkspaceClient | None = None) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{get_databricks_host(workspace_client)}{path}"


def get_session_id(request: ResponsesAgentRequest) -> str | None:
    if request.context and request.context.conversation_id:
        return str(request.context.conversation_id)
    custom_inputs = dict(request.custom_inputs or {})
    value = custom_inputs.get("session_id") or custom_inputs.get("thread_id")
    return str(value) if value else None


def get_user_id(
    request: ResponsesAgentRequest,
    workspace_client: WorkspaceClient | None = None,
) -> str | None:
    if workspace_client is not None:
        try:
            current_user = workspace_client.current_user.me()
            verified_id = getattr(current_user, "id", None) or getattr(current_user, "user_name", None)
            if verified_id:
                return str(verified_id)
        except Exception:
            logging.getLogger(__name__).warning(
                "Unable to resolve the verified OBO user identity.", exc_info=True
            )
        return None

    custom_inputs = dict(request.custom_inputs or {})
    value = custom_inputs.get("user_id")
    if value:
        return str(value)
    if request.context and getattr(request.context, "user_id", None):
        return str(request.context.user_id)
    return None


def get_user_workspace_client() -> WorkspaceClient:
    """Build a workspace client from the App OBO access token."""
    token = get_request_headers().get("x-forwarded-access-token")
    if not token:
        raise RuntimeError(
            "An OBO access token is required. Invoke the supervisor through a Databricks App."
        )
    return WorkspaceClient(token=token, auth_type="pat")


def new_session_id() -> str:
    return str(uuid4())


async def process_agent_stream_events(
    async_stream: AsyncIterator[StreamEvent],
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    """Convert OpenAI Agents SDK stream events to MLflow Responses events."""
    current_item_id = str(uuid4())
    async for event in async_stream:
        if event.type == "raw_response_event":
            event_data = event.data.model_dump()
            if event_data["type"] == "response.output_item.added":
                current_item_id = str(uuid4())
                event_data["item"]["id"] = current_item_id
            elif event_data.get("item") is not None and event_data["item"].get("id"):
                event_data["item"]["id"] = current_item_id
            elif event_data.get("item_id"):
                event_data["item_id"] = current_item_id
            yield event_data
        elif event.type == "run_item_stream_event" and event.item.type == "tool_call_output_item":
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=event.item.to_input_item(),
            )


def log_mcp_failure(server_name: str, error: Exception) -> None:
    logging.getLogger(__name__).warning(
        "Managed MCP server unavailable: %s (%s)", server_name, error
    )