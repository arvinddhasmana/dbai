"""GlobalMart vector-search contract agent and MLflow handlers."""

import os
import uuid

import mlflow
from agents import Agent, ModelSettings, Runner, set_default_openai_api, set_default_openai_client
from databricks_openai import AsyncDatabricksOpenAI
from mlflow.genai.agent_server import invoke
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from agent_server.data_tools import search_vendor_contracts


CATALOG = os.getenv("DBAI_CATALOG", "globalmart")
set_default_openai_client(AsyncDatabricksOpenAI())
set_default_openai_api("chat_completions")
mlflow.openai.autolog()

ANSWER_INSTRUCTIONS = f"""
You are the GlobalMart Contract Intelligence agent. Answer only questions
that can be grounded in current vendor-contract evidence. Use
search_vendor_contracts for every substantive question. Never generate SQL,
query inventory tables, access ingestion tables, or invent contract facts.

The search tool uses the governed {CATALOG}.supply_chain.search_vendor_contracts
function backed by the managed AI Search index. Pass vendor_id, support_tier,
and region when the user provides them. Cite evidence as [source_file, chunk N]
using returned source_file and chunk_index values.

An empty successful result means no active searchable contract evidence was
found. A result with ok=false is an operational failure: preserve its
error_code and message, and do not fabricate an answer or use another data
source. Keep answers concise and do not mention internal orchestration.
""".strip()


def create_agent():
    return Agent(
        name="GlobalMart Contract Intelligence Agent",
        instructions=ANSWER_INSTRUCTIONS,
        model=os.getenv("MODEL_ENDPOINT", "databricks-llama-4-maverick"),
        model_settings=ModelSettings(temperature=0),
        tools=[search_vendor_contracts],
    )


def _prepare_runner_input(items):
    prepared = []
    for item in items:
        if hasattr(item, "model_dump"):
            message = item.model_dump(exclude_none=True)
        elif isinstance(item, dict):
            message = {key: value for key, value in item.items() if value is not None}
        else:
            continue
        if message.get("type") == "message":
            message.pop("type", None)
        if message.get("role") in {"user", "assistant"}:
            content = message.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") in {"output_text", "text"}
                    and part.get("text")
                )
            prepared.append({"role": message["role"], "content": content})
            continue
        prepared.append(message)
    return prepared


def _response(text):
    return ResponsesAgentResponse(
        output=[
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    )


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    result = await Runner.run(create_agent(), _prepare_runner_input(request.input))
    return _response(result.final_output or "I could not produce an answer.")
