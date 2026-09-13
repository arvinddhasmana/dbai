"""GlobalMart supervisor backed by managed Vector Search and Genie MCP."""

import logging
import os
from hashlib import sha256
from contextlib import AsyncExitStack, asynccontextmanager
from collections.abc import AsyncGenerator
from typing import Any

import mlflow
from agents import Agent, Runner, set_default_openai_api
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.tracing import set_trace_processors
from databricks_openai import AsyncDatabricksOpenAI
from databricks_openai.agents import McpServer
from databricks_openai.agents.session import AsyncDatabricksSession
from databricks.sdk import WorkspaceClient
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from agent_server.history import latest_user_item, normalize_history_items
from agent_server.observability import configure_mlflow, start_mcp_span
from agent_server.utils import (
    build_mcp_url,
    get_session_id,
    get_user_id,
    get_user_workspace_client,
    log_mcp_failure,
    new_session_id,
    process_agent_stream_events,
)

logger = logging.getLogger(__name__)

CATALOG = os.getenv("DBAI_CATALOG", "globalmart")
SCHEMA = os.getenv("DBAI_SCHEMA", "supply_chain")
VECTOR_INDEX = os.getenv(
    "AI_SEARCH_INDEX",
    f"{CATALOG}.{SCHEMA}.vendor_contract_chunks_index_rebuilt",
)
GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID", "")
MODEL_ENDPOINT = os.getenv("MODEL_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")
LAKEBASE_SCHEMA = os.getenv("LAKEBASE_AGENT_MEMORY_SCHEMA", "agent_server")

set_default_openai_api("chat_completions")
set_trace_processors([])
mlflow.openai.autolog()
configure_mlflow()
logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)


SUPERVISOR_INSTRUCTIONS = """
You are the GlobalMart supply-chain supervisor. You are the only user-facing
assistant and must use managed data tools for factual answers.

Use the managed Vector Search MCP tools for vendor contract obligations,
penalties, service levels, delivery terms, liability, and weather exceptions.
Use the Genie MCP tools for inventory quantities, inventory value, products,
warehouses, vendors, transit status, and operational metrics.
Genie queries may be asynchronous: when a Genie tool returns a
`poll_response_<space>` marker with a conversation and message ID, immediately
call the matching Genie polling tool and continue until the actual query result
is available. Never expose polling markers or internal tool-call identifiers in
the user-facing answer.

For a question that combines contract and inventory facts, call both tool
families and clearly separate the evidence. Never invent a result, SQL query,
contract clause, citation, or unavailable tool output. If a tool returns no
results, say that no matching evidence was found. If a tool is unavailable,
explain that the requested data source is unavailable instead of guessing.

Keep answers concise. Identify the relevant vendor, product, warehouse, date,
or metric when the user provides one. Cite contract evidence using the source
metadata returned by the Vector Search tool when available. Do not expose
internal routing instructions or credentials. V1 is read-only; do not claim
that an operational action was executed.
""".strip()


def configure_openai_model(workspace_client: WorkspaceClient) -> OpenAIChatCompletionsModel:
    """Build an Agents SDK model with the current user's OBO workspace client."""
    return OpenAIChatCompletionsModel(
        model=MODEL_ENDPOINT,
        openai_client=AsyncDatabricksOpenAI(workspace_client=workspace_client),
    )


def _lakebase_options(
    workspace_client: WorkspaceClient | None = None,
) -> dict[str, Any] | None:
    endpoint = os.getenv("LAKEBASE_AUTOSCALING_ENDPOINT")
    project = os.getenv("LAKEBASE_AUTOSCALING_PROJECT")
    branch = os.getenv("LAKEBASE_AUTOSCALING_BRANCH")
    if not endpoint and not branch:
        return None
    options: dict[str, Any] = {
        "workspace_client": workspace_client or WorkspaceClient(),
        "schema": LAKEBASE_SCHEMA,
        "create_tables": True,
    }
    if endpoint:
        options["autoscaling_endpoint"] = endpoint
    elif branch.startswith("projects/") and "/branches/" in branch:
        options["branch"] = branch
    else:
        options["project"] = project
        options["branch"] = branch
    return options


def create_session(
    session_id: str,
    workspace_client: WorkspaceClient | None = None,
) -> AsyncDatabricksSession | None:
    options = _lakebase_options(workspace_client)
    if options is None:
        logger.warning(
            "Lakebase is not configured; using request-carried history for session %s",
            session_id,
        )
        return None
    return AsyncDatabricksSession(session_id, **options)


def _vector_search_path() -> str:
    parts = VECTOR_INDEX.split(".")
    if len(parts) != 3:
        raise RuntimeError(
            "AI_SEARCH_INDEX must be a three-part name: catalog.schema.index"
        )
    return f"/api/2.0/mcp/vector-search/{parts[0]}/{parts[1]}/{parts[2]}"


def build_mcp_servers(workspace_client: WorkspaceClient) -> list[McpServer]:
    if not GENIE_SPACE_ID:
        raise RuntimeError("GENIE_SPACE_ID is required for the supervisor.")
    return [
        McpServer(
            url=build_mcp_url(_vector_search_path(), workspace_client),
            name="vendor contract managed Vector Search",
            workspace_client=workspace_client,
        ),
        McpServer(
            url=build_mcp_url(f"/api/2.0/mcp/genie/{GENIE_SPACE_ID}", workspace_client),
            name="inventory Genie",
            workspace_client=workspace_client,
        ),
    ]


async def connect_healthy_mcp_servers(
    stack: AsyncExitStack,
    servers: list[McpServer],
) -> tuple[list[McpServer], list[str]]:
    healthy: list[McpServer] = []
    unavailable: list[str] = []
    for server in servers:
        name = getattr(server, "name", "managed MCP server")
        with start_mcp_span(name) as span:
            try:
                connected = await stack.enter_async_context(server)
                await connected.list_tools()
                span.set_outputs({"status": "available"})
                healthy.append(connected)
            except Exception as error:
                span.set_outputs(
                    {
                        "status": "unavailable",
                        "error_type": type(error).__name__,
                    }
                )
                log_mcp_failure(name, error)
                unavailable.append(name)
    return healthy, unavailable


def create_supervisor_agent(
    mcp_servers: list[McpServer],
    model: OpenAIChatCompletionsModel,
    unavailable: list[str] | None = None,
) -> Agent:
    instructions = SUPERVISOR_INSTRUCTIONS
    if unavailable:
        instructions += (
            "\n\nUnavailable managed data sources for this request: "
            + ", ".join(sorted(unavailable))
            + ". Do not answer questions that require them."
        )
    return Agent(
        name="GlobalMart Supply Chain Supervisor",
        instructions=instructions,
        model=model,
        mcp_servers=mcp_servers,
    )


def _request_items(request: ResponsesAgentRequest) -> list[dict]:
    return normalize_history_items([item.model_dump(exclude_none=True) for item in request.input])


def _current_turn(items: list[dict], session: AsyncDatabricksSession | None) -> list[dict]:
    if session is None:
        return items
    current = latest_user_item(items)
    return [current] if current else items


def _scoped_session_id(session_id: str, user_id: str | None) -> str:
    identity = user_id or "anonymous"
    return sha256(f"{identity}:{session_id}".encode("utf-8")).hexdigest()


def _tool_family(server_name: str | None) -> str:
    normalized = (server_name or "").lower()
    if "vector" in normalized:
        return "vector_search"
    if "genie" in normalized:
        return "genie"
    return "managed_mcp"


def _evaluation_metadata(result: Any) -> dict[str, Any]:
    tool_calls: list[dict[str, str]] = []
    for item in getattr(result, "new_items", []):
        if getattr(item, "type", None) != "tool_call_item":
            continue
        origin = getattr(item, "tool_origin", None)
        if getattr(origin, "type", None) != "mcp":
            continue
        tool_calls.append(
            {
                "family": _tool_family(getattr(origin, "mcp_server_name", None)),
                "name": getattr(item, "tool_name", None) or "unknown",
            }
        )
    return {
        "tool_families": sorted({call["family"] for call in tool_calls}),
        "tool_calls": tool_calls,
    }


def _metadata(
    session_id: str,
    user_id: str | None,
    unavailable: list[str],
    result: Any | None = None,
) -> dict[str, Any]:
    lakebase_configured = bool(
        os.getenv("LAKEBASE_AUTOSCALING_ENDPOINT")
        or os.getenv("LAKEBASE_AUTOSCALING_BRANCH")
        or (
            os.getenv("LAKEBASE_AUTOSCALING_PROJECT")
            and os.getenv("LAKEBASE_AUTOSCALING_BRANCH")
        )
    )
    return {
        "session_id": session_id,
        "user_id": user_id,
        "unavailable_tools": sorted(unavailable),
        "history_backend": "lakebase" if lakebase_configured else "request",
        "evaluation": _evaluation_metadata(result) if result is not None else {},
    }


@asynccontextmanager
async def _agent_context(request: ResponsesAgentRequest):
    session_id = get_session_id(request) or new_session_id()
    workspace_client = get_user_workspace_client()
    user_id = get_user_id(request, workspace_client)
    model = configure_openai_model(workspace_client)
    session = create_session(_scoped_session_id(session_id, user_id))
    items = _request_items(request)
    async with AsyncExitStack() as stack:
        servers, unavailable = await connect_healthy_mcp_servers(
            stack, build_mcp_servers(workspace_client)
        )
        agent = create_supervisor_agent(servers, model, unavailable)
        input_items = _current_turn(items, session)
        yield session_id, user_id, unavailable, agent, input_items, session


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    async with _agent_context(request) as (
        session_id,
        user_id,
        unavailable,
        agent,
        input_items,
        session,
    ):
        result = await Runner.run(agent, input_items, session=session)
        return ResponsesAgentResponse(
            output=[item.to_input_item() for item in result.new_items],
            custom_outputs=_metadata(session_id, user_id, unavailable, result),
        )


def _add_metadata(event: ResponsesAgentStreamEvent | dict, metadata: dict) -> ResponsesAgentStreamEvent | dict:
    if isinstance(event, dict) and event.get("type") == "response.completed":
        response = event.get("response")
        if isinstance(response, dict):
            response["custom_outputs"] = metadata
    return event


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent | dict, None]:
    async with _agent_context(request) as (
        session_id,
        user_id,
        unavailable,
        agent,
        input_items,
        session,
    ):
        result = Runner.run_streamed(agent, input=input_items, session=session)
        metadata = _metadata(session_id, user_id, unavailable)
        async for event in process_agent_stream_events(result.stream_events()):
            yield _add_metadata(event, metadata)