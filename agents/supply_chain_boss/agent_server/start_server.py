"""Serve the Boss chat/approval routes and MLflow Responses AgentServer.

The custom FastAPI routes provide the approval UI, while MLflow's existing
``/invocations`` and ``/responses`` handlers are reused for agent clients.
Incoming sampled trace context is restored at the request boundary so spans
from the Boss and its Specialist call can share one trace.
"""

import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mlflow.genai.agent_server import AgentServer
from mlflow.tracing import set_tracing_context_from_http_request_headers
from pydantic import BaseModel, ConfigDict

from agent_server.agent import (
    _app_base_url,
    _trusted_identity,
    _user_access_token,
    close_runtime,
    get_runtime,
    initialize,
)
from agent_server.config import get_settings
from agent_server.workflow import (
    decide_manager_approval,
    handle_message,
    manager_approval_details,
)

logger = logging.getLogger(__name__)
static_path = Path(__file__).parents[1] / "static"

agent_server = AgentServer("ResponsesAgent", enable_chat_proxy=False)
app = agent_server.app


def _traceparent_is_sampled(traceparent: str) -> bool:
    """Check the W3C trace-flags sampled bit; malformed values fail open to local tracing."""
    parts = traceparent.split("-")
    if len(parts) != 4 or len(parts[3]) != 2:
        return True
    try:
        flags = int(parts[3], 16)
    except ValueError:
        return True
    return bool(flags & 0x01)


@contextmanager
def _incoming_mlflow_trace_context(request: Request):
    """Restore a trusted, sampled incoming MLflow trace context for this request."""
    traceparent = request.headers.get("traceparent")
    if not traceparent or not _traceparent_is_sampled(traceparent):
        if traceparent:
            logger.info(
                "Incoming MLflow trace context is unsampled; starting a new local trace."
            )
        yield
        return

    context = set_tracing_context_from_http_request_headers(dict(request.headers))
    try:
        context.__enter__()
    except Exception:
        logger.warning(
            "Unable to activate incoming MLflow trace context; continuing with a new trace.",
            exc_info=True,
        )
        yield
        return

    try:
        yield
    finally:
        context.__exit__(*sys.exc_info())


@app.middleware("http")
async def mlflow_trace_context(request: Request, call_next):
    """Keep trace context active across the complete async request handler."""
    with _incoming_mlflow_trace_context(request):
        return await call_next(request)


class ChatRequest(BaseModel):
    """Request body for the custom chat API; unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    message: str
    conversation_id: str | None = None


class ApprovalRequest(BaseModel):
    """Explicit binary decision accepted by the manager-approval endpoint."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]


@app.on_event("startup")
async def startup() -> None:
    """Initialize shared model, graph, and checkpoint resources before serving."""
    await initialize(get_settings())


@app.on_event("shutdown")
async def shutdown() -> None:
    """Close the runtime's pooled resources during graceful shutdown."""
    await close_runtime()


app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the Boss chat page."""
    return FileResponse(static_path / "index.html")


@app.get("/approval/{thread_id}", include_in_schema=False)
async def approval_page(thread_id: str) -> FileResponse:
    """Serve approval UI only for a syntactically valid opaque thread key."""
    if len(thread_id) != 64 or any(char not in "0123456789abcdef" for char in thread_id):
        raise HTTPException(status_code=404, detail="Approval request not found.")
    return FileResponse(static_path / "approval.html")


@app.get("/api/approvals/{thread_id}")
async def get_approval(thread_id: str) -> dict:
    """Return details for an active matching manager-approval interrupt."""
    if len(thread_id) != 64 or any(char not in "0123456789abcdef" for char in thread_id):
        raise HTTPException(status_code=404, detail="Approval request not found.")
    try:
        return await manager_approval_details(get_runtime(), thread_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        logger.exception("Approval details could not be loaded.")
        raise HTTPException(
            status_code=502, detail="Approval details could not be loaded."
        ) from error


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> dict:
    """Validate a chat turn, resolve ingress identity, and invoke the graph."""
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty.")
    headers = dict(request.headers)
    _, user_id = _trusted_identity(headers)
    if not payload.conversation_id:
        raise HTTPException(
            status_code=400,
            detail="conversation_id is required for persistent conversations.",
        )
    try:
        return await handle_message(
            runtime=get_runtime(),
            conversation_id=payload.conversation_id,
            message=payload.message.strip(),
            user_id=user_id,
            app_base_url=_app_base_url(headers),
            user_access_token=_user_access_token(headers),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        logger.exception("Boss chat request failed.")
        raise HTTPException(
            status_code=502, detail="Boss Agent request failed; no success is implied."
        ) from error


@app.post("/api/approvals/{thread_id}")
async def record_approval(
    thread_id: str, payload: ApprovalRequest, request: Request
) -> dict:
    """Record one explicit approval decision and resume the paused graph.

    This MVP does not yet enforce a manager role; the limitation and required
    authorization/CSRF hardening are documented in the app README.
    """
    if len(thread_id) != 64 or any(char not in "0123456789abcdef" for char in thread_id):
        raise HTTPException(status_code=404, detail="Approval request not found.")
    try:
        # MVP allows any authenticated App user; manager-role authorization
        # and verified approver identity remain explicit hardening tasks.
        # TODO: Add CSRF/origin protection before expanding App access.
        return await decide_manager_approval(
            get_runtime(),
            thread_id,
            payload.decision == "approve",
            user_access_token=_user_access_token(dict(request.headers)),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        logger.exception("Approval decision could not be recorded.")
        raise HTTPException(
            status_code=502, detail="Approval decision could not be completed."
        ) from error


_invocations_endpoint = next(
    route.endpoint for route in app.routes if route.path == "/invocations"
)
_responses_endpoint = next(
    route.endpoint for route in app.routes if route.path == "/responses"
)


@app.post("/api/invocations")
async def api_invocations(request: Request):
    """Expose MLflow's registered invocations endpoint under the App API prefix."""
    return await _invocations_endpoint(request)


@app.post("/api/responses")
async def api_responses(request: Request):
    """Expose MLflow's registered Responses endpoint under the App API prefix."""
    return await _responses_endpoint(request)


def main() -> None:
    """Run the configured MLflow AgentServer application."""
    agent_server.run(app_import_string="agent_server.start_server:app")
