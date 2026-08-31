"""Start the MLflow AgentServer and custom chat page."""

from pathlib import Path

from dotenv import load_dotenv
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mlflow.genai.agent_server import AgentServer

load_dotenv(Path(__file__).parents[2] / ".env")

import agent_server.agent  # noqa: F401


APP_ROOT = Path(__file__).parents[1]
agent_server = AgentServer("ResponsesAgent", enable_chat_proxy=False)
app = agent_server.app
app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(APP_ROOT / "static" / "index.html")


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


def main():
    agent_server.run(app_import_string="agent_server.start_server:app")
