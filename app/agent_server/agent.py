"""GlobalMart supply-chain agent definition and MLflow handlers."""

import logging
import os
import re

import mlflow
from agents import Agent, Runner, set_default_openai_api, set_default_openai_client
from databricks_openai import AsyncDatabricksOpenAI
from mlflow.genai.agent_server import invoke
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from agent_server.data_tools import _lookup_inventory, _search_vendor_contracts


logger = logging.getLogger(__name__)
CATALOG = os.getenv("DBAI_CATALOG", "globalmart")

set_default_openai_client(AsyncDatabricksOpenAI())
set_default_openai_api("chat_completions")
mlflow.openai.autolog()


ANSWER_INSTRUCTIONS = """
You are the final GlobalMart Supply Chain Intelligence answer writer.
Answer the user's question using the AUTHORITATIVE RESULTS included in the
latest user message. Never invent facts, SQL, tool calls, or citations.

For inventory, treat matching_rows and delayed_rows as authoritative. If
delayed_rows is empty, do not claim a product is delayed; state its actual
transit_status from matching_rows when available. Calculate or report values
only from inventory_value_usd. For contract questions, cite evidence as
[source_file, chunk N] using the returned source_file and chunk_index. If no
contract rows are returned, state that active searchable evidence is
unavailable. Keep the answer concise and do not mention internal orchestration.
""".strip()


def create_agent():
    return Agent(
        name="GlobalMart Supply Chain Assistant",
        instructions=ANSWER_INSTRUCTIONS,
        model=os.getenv("MODEL_ENDPOINT", "databricks-llama-4-maverick"),
    )


def _latest_user_question(messages):
    for message in reversed(messages):
        if message.get("role") == "user" and message.get("content"):
            return message["content"]
    return ""


def _vendor_id_from_question(question):
    match = re.search(r"\bVEND[-_ ]?(\d+)\b", question, re.IGNORECASE)
    return f"VEND-{match.group(1)}" if match else None


def _product_from_question(question):
    match = re.search(r"\b(thermal\s+winter\s+coats?)\b", question, re.IGNORECASE)
    return match.group(1) if match else None


def _ground_question(messages):
    question = _latest_user_question(messages)
    lowered = question.lower()
    results = []

    inventory_question = any(
        term in lowered
        for term in ("inventory", "delayed", "transit", "stock", "product")
    )
    contract_question = any(
        term in lowered
        for term in ("contract", "weather", "penalty", "liability", "service level")
    )
    if inventory_question:
        results.append(
            "AUTHORITATIVE INVENTORY RESULTS:\n"
            + _lookup_inventory(
                product_name=_product_from_question(question),
                vendor_id=_vendor_id_from_question(question),
                delayed_only="delayed" in lowered,
            )
        )
    if contract_question:
        results.append(
            "AUTHORITATIVE CONTRACT RESULTS:\n"
            + _search_vendor_contracts(
                search_text=question,
                vendor_id=_vendor_id_from_question(question),
            )
        )
    if not results:
        return messages

    grounded_content = question + "\n\n" + "\n\n".join(results)
    return messages[:-1] + [{"role": "user", "content": grounded_content}]


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


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    messages = _prepare_runner_input(request.input)
    result = await Runner.run(create_agent(), _ground_question(messages))
    return ResponsesAgentResponse(
        output=[item.to_input_item() for item in result.new_items]
    )
