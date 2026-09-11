"""GlobalMart vector-search contract agent and MLflow handlers."""

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncGenerator

import mlflow
from agents import Agent, ModelSettings, Runner, set_default_openai_api, set_default_openai_client
from agents.tracing import set_trace_processors
from databricks_openai import AsyncDatabricksOpenAI
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse, ResponsesAgentStreamEvent

from agent_server.data_tools import _search_vendor_contracts
from agent_server.history import normalize_history_items
from agent_server.observability import configure_mlflow, start_agent_span
from agent_server.utils import get_session_id, process_agent_stream_events


CATALOG = os.getenv("DBAI_CATALOG", "globalmart")
AGENT_NAME = "agent-supply-chain-contract-ka"
USE_AI_GATEWAY = os.getenv("USE_AI_GATEWAY", "false").strip().lower() in {"1", "true", "yes"}
DIRECT_MODEL_ENDPOINT = os.getenv("MODEL_ENDPOINT", "databricks-llama-4-maverick")
AI_GATEWAY_ENDPOINT = os.getenv(
    "AI_GATEWAY_ENDPOINT",
    "agent-supply-chain-contract-ka-model",
)


def _model_endpoint():
    return AI_GATEWAY_ENDPOINT if USE_AI_GATEWAY else DIRECT_MODEL_ENDPOINT


set_default_openai_client(AsyncDatabricksOpenAI(use_ai_gateway=USE_AI_GATEWAY))
set_default_openai_api("chat_completions")
set_trace_processors([])
mlflow.openai.autolog()
configure_mlflow()

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


def create_answer_agent():
    return Agent(
        name=f"{AGENT_NAME}-answer",
        instructions=(
            "Answer the user's contract question only from the retrieved evidence "
            "provided in the conversation. Cite evidence as [source_file, chunk N]. "
            "If ok=false, report the supplied error_code and message without inventing "
            "an answer. If row_count is zero, say that no active contract evidence "
            "was found. Keep the answer concise."
        ),
        model=_model_endpoint(),
        model_settings=ModelSettings(temperature=0),
    )


def _prepare_runner_input(items):
    prepared = []
    for item in items:
        if hasattr(item, "model_dump"):
            prepared.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            prepared.append({key: value for key, value in item.items() if value is not None})
        else:
            continue
    return normalize_history_items(prepared)


def _response(text, search_result):
    evidence = _parse_evidence(search_result)
    return ResponsesAgentResponse(
        output=[
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        custom_outputs={"contract_evidence": evidence},
    )


def _parse_evidence(search_result):
    try:
        return json.loads(search_result)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error_code": "CONTRACT_SEARCH_INVALID_RESPONSE",
            "message": "Contract search returned an invalid response.",
        }


def _latest_user_text(items):
    for item in reversed(items):
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("text")
                )
    return ""


def _search_parameters(question, context=""):
    vendor_matches = re.findall(
        r"\bVEND[-_]?\d+\b",
        f"{question}\n{context}",
        re.IGNORECASE,
    )
    vendor_id = vendor_matches[-1].upper().replace("_", "-") if vendor_matches else None
    return question, vendor_id


def _conversation_messages(items):
    return [
        {"role": item["role"], "content": item["content"]}
        for item in items
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and item.get("content")
    ]


def _search_context(items):
    user_messages = [
        item["content"]
        for item in items
        if isinstance(item, dict)
        and item.get("role") == "user"
        and isinstance(item.get("content"), str)
    ]
    return "\n".join(user_messages[-3:])


def _update_trace_session(request):
    if session_id := get_session_id(request):
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})


async def _run_answer(question, search_result, history):
    answer_input = [
        {
            "role": "system",
            "content": (
                "Use only the current retrieved contract evidence for factual claims. "
                "Conversation history is included only to resolve follow-up references.\n\n"
                f"Retrieved contract evidence JSON:\n{search_result}"
            ),
        },
        *_conversation_messages(history),
    ]
    return await Runner.run(create_answer_agent(), answer_input)


def _stream_event_with_evidence(event, evidence):
    if isinstance(event, dict) and event.get("type") == "response.completed":
        response = event.get("response")
        if isinstance(response, dict):
            response["custom_outputs"] = {"contract_evidence": evidence}
    return event


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    _update_trace_session(request)
    prepared_input = _prepare_runner_input(request.input)
    question = _latest_user_text(prepared_input)
    context = _search_context(prepared_input)
    search_text, vendor_id = _search_parameters(question, context)
    with start_agent_span(
        "contract_agent.invoke",
        {
            "vendor_filter_present": bool(vendor_id),
            "question_length": len(question),
            "model_endpoint": _model_endpoint(),
        },
    ) as span:
        search_result = await asyncio.to_thread(
            _search_vendor_contracts,
            context or search_text,
            vendor_id=vendor_id,
        )
        result = await _run_answer(question, search_result, prepared_input)
        response = _response(result.final_output or "I could not produce an answer.", search_result)
        evidence = response.custom_outputs["contract_evidence"]
        span.set_outputs(
            {
                "ok": evidence.get("ok", False),
                "row_count": evidence.get("row_count", 0),
                "error_code": evidence.get("error_code"),
            }
        )
        return response


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent | dict, None]:
    _update_trace_session(request)
    prepared_input = _prepare_runner_input(request.input)
    question = _latest_user_text(prepared_input)
    context = _search_context(prepared_input)
    search_text, vendor_id = _search_parameters(question, context)
    with start_agent_span(
        "contract_agent.stream",
        {
            "vendor_filter_present": bool(vendor_id),
            "question_length": len(question),
            "model_endpoint": _model_endpoint(),
        },
    ) as span:
        search_result = await asyncio.to_thread(
            _search_vendor_contracts,
            context or search_text,
            vendor_id=vendor_id,
        )
        evidence = _parse_evidence(search_result)
        result = Runner.run_streamed(
            create_answer_agent(),
            input=[
                {
                    "role": "system",
                    "content": (
                        "Use only the current retrieved contract evidence for factual claims. "
                        "Conversation history is included only to resolve follow-up references.\n\n"
                        f"Retrieved contract evidence JSON:\n{search_result}"
                    ),
                },
                *_conversation_messages(prepared_input),
            ],
        )
        async for event in process_agent_stream_events(result.stream_events()):
            yield _stream_event_with_evidence(event, evidence)
        span.set_outputs(
            {
                "ok": evidence.get("ok", False),
                "row_count": evidence.get("row_count", 0),
                "error_code": evidence.get("error_code"),
            }
        )
