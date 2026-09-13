import sys
from contextlib import contextmanager
from pathlib import Path
import logging

from fastapi import Request
from dotenv import load_dotenv
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mlflow.genai.agent_server import AgentServer, setup_mlflow_git_based_version_tracking
from mlflow.tracing import set_tracing_context_from_http_request_headers

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

logger = logging.getLogger(__name__)

import agent_server.agent  # noqa: E402,F401

agent_server = AgentServer("ResponsesAgent", enable_chat_proxy=False)
app = agent_server.app


@contextmanager
def _incoming_mlflow_trace_context(request: Request):
    if not request.headers.get("traceparent"):
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
    with _incoming_mlflow_trace_context(request):
        return await call_next(request)

# Databricks App token authentication exposes API routes under /api/.
_mlflow_invocations_endpoint = next(
    route.endpoint for route in app.routes if route.path == "/invocations"
)
_mlflow_responses_endpoint = next(
    route.endpoint for route in app.routes if route.path == "/responses"
)


@app.post("/api/invocations")
async def api_invocations(request: Request):
    return await _mlflow_invocations_endpoint(request)


@app.post("/api/responses")
async def api_responses(request: Request):
    return await _mlflow_responses_endpoint(request)


app.mount("/static", StaticFiles(directory=Path(__file__).parents[1] / "static"), name="static")
try:
    setup_mlflow_git_based_version_tracking()
except Exception:
    logger.warning("MLflow git version tracking setup failed; continuing without it.", exc_info=True)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(Path(__file__).parents[1] / "static" / "index.html")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


def main():
    agent_server.run(app_import_string="agent_server.start_server:app")