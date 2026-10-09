"""Host the custom Specialist UI and MLflow Responses AgentServer.

The template chat proxy is disabled because the repository's UI consumes the
Responses API directly and renders contract citations as expandable evidence.
Git-based MLflow version tracking is best-effort so its setup cannot prevent
the serving process from starting.
"""

import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mlflow.genai.agent_server import AgentServer, setup_mlflow_git_based_version_tracking

load_dotenv(Path(__file__).parents[4] / ".env")

import agent_server.agent  # noqa: F401,E402


APP_ROOT = Path(__file__).parents[2]
logger = logging.getLogger(__name__)
agent_server = AgentServer("ResponsesAgent", enable_chat_proxy=False)
app = agent_server.app
app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")
try:
    setup_mlflow_git_based_version_tracking()
except Exception:
    logger.warning("MLflow git version tracking setup failed; continuing without it.", exc_info=True)


@app.get("/", include_in_schema=False)
async def index():
    """Serve the custom contract chat interface at the App root."""
    return FileResponse(APP_ROOT / "static" / "index.html")


@app.get("/health", include_in_schema=False)
async def health():
    """Expose the lightweight liveness response used by App health checks."""
    return {"status": "ok"}


def main():
    """Run MLflow's AgentServer with the FastAPI app defined in this module."""
    agent_server.run(app_import_string="agent_server.start_server:app")
