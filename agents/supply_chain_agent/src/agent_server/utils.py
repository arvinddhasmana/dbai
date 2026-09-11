"""Authentication, session, and streaming helpers for Databricks Apps."""

from collections.abc import AsyncGenerator, AsyncIterator
from uuid import uuid4

from agents.result import StreamEvent
from databricks.sdk import WorkspaceClient
from mlflow.genai.agent_server import get_request_headers
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentStreamEvent


def get_session_id(request: ResponsesAgentRequest) -> str | None:
    if request.context and request.context.conversation_id:
        return request.context.conversation_id
    if request.custom_inputs and isinstance(request.custom_inputs, dict):
        session_id = request.custom_inputs.get("session_id")
        return str(session_id) if session_id else None
    return None


async def process_agent_stream_events(
    async_stream: AsyncIterator[StreamEvent],
) -> AsyncGenerator[ResponsesAgentStreamEvent | dict, None]:
    """Convert OpenAI Agents events to MLflow Responses stream events."""
    current_item_id = str(uuid4())
    async for event in async_stream:
        if event.type == "raw_response_event":
            event_data = event.data.model_dump()
            if event_data["type"] == "response.output_item.added":
                current_item_id = str(uuid4())
                event_data["item"]["id"] = current_item_id
            elif event_data.get("item") is not None and event_data["item"].get("id") is not None:
                event_data["item"]["id"] = current_item_id
            elif event_data.get("item_id") is not None:
                event_data["item_id"] = current_item_id
            yield event_data
        elif event.type == "run_item_stream_event" and event.item.type == "tool_call_output_item":
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=event.item.to_input_item(),
            )


def get_user_workspace_client():
    """Return an on-behalf-of client when the request carries a user token."""
    try:
        token = get_request_headers().get("x-forwarded-access-token")
    except Exception:
        token = None
    if token:
        return WorkspaceClient(token=token, auth_type="pat")
    return WorkspaceClient()
