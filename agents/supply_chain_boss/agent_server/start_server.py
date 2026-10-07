"""FastAPI host for the Boss Agent and approval UI."""

import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mlflow.genai.agent_server import AgentServer
from pydantic import BaseModel, ConfigDict

from agent_server.agent import (
    _app_base_url,
    _trusted_identity,
    _user_access_token,
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


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    conversation_id: str | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]


@app.on_event("startup")
async def startup() -> None:
    await initialize(get_settings())


app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_path / "index.html")


@app.get("/approval/{thread_id}", include_in_schema=False)
async def approval_page(thread_id: str) -> FileResponse:
    if len(thread_id) != 64 or any(char not in "0123456789abcdef" for char in thread_id):
        raise HTTPException(status_code=404, detail="Approval request not found.")
    return FileResponse(static_path / "approval.html")


@app.get("/api/approvals/{thread_id}")
async def get_approval(thread_id: str) -> dict:
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
            app_base_url=_app_base_url(headers, get_settings()),
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
    return await _invocations_endpoint(request)


@app.post("/api/responses")
async def api_responses(request: Request):
    return await _responses_endpoint(request)


def main() -> None:
    agent_server.run(app_import_string="agent_server.start_server:app")
