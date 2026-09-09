"""Adapters that connect evaluation code to the deployed agent and judge model."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from evaluation.judge import JudgeResult, build_prompt, parse_result
from evaluation.runner import EvaluationCase


@dataclass(frozen=True)
class AgentEvaluationResult:
    answer: str
    evidence: dict[str, Any]


class AgentInvoker(Protocol):
    def __call__(self, request: dict[str, Any]) -> Any: ...


class JudgeCompletions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class JudgeClient(Protocol):
    chat: Any


DEFAULT_JUDGE_MODEL = "databricks-meta-llama-3-1-8b-instruct"


def _agent_invocation_url(url: str) -> str:
    """Use the MLflow AgentServer invocation route unless one was supplied."""
    return url.rstrip("/") if url.rstrip("/").endswith("/invocations") else f"{url.rstrip('/')}/invocations"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    raise ValueError("Agent response must be a mapping or model with model_dump()")


def _extract_answer(response: dict[str, Any]) -> str:
    outputs = response.get("output", [])
    for item in reversed(outputs):
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        content = item.get("content", [])
        for part in reversed(content if isinstance(content, list) else []):
            if part.get("type") in {"output_text", "text"} and part.get("text"):
                return str(part["text"])
    raise ValueError("Agent response did not contain an assistant text message")


def _extract_evidence(response: dict[str, Any]) -> dict[str, Any]:
    evidence = response.get("custom_outputs", {}).get("contract_evidence", {})
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    if not isinstance(evidence, dict):
        raise ValueError("Agent response did not contain contract_evidence")
    return evidence


def invoke_agent(case: EvaluationCase, invoker: AgentInvoker) -> AgentEvaluationResult:
    """Invoke an App-compatible callable and extract answer plus retrieval evidence."""
    request = {"input": [{"role": "user", "content": case.question}]}
    response = _as_dict(invoker(request))
    return AgentEvaluationResult(
        answer=_extract_answer(response),
        evidence=_extract_evidence(response),
    )


def _response_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        raise ValueError("Judge model returned no choices")
    message = choices[0].message if hasattr(choices[0], "message") else choices[0].get("message", {})
    content = message.content if hasattr(message, "content") else message.get("content")
    if not content:
        raise ValueError("Judge model returned an empty message")
    return str(content)


def judge_agent_result(
    case: EvaluationCase,
    result: AgentEvaluationResult,
    client: JudgeClient,
    model: str,
) -> JudgeResult:
    """Score one agent result through a Databricks OpenAI-compatible client."""
    prompt = build_prompt(
        question=case.question,
        answer=result.answer,
        contexts=result.evidence.get("rows", []),
        required_facts=list(case.required_facts),
    )
    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return parse_result(_response_text(completion))


def create_databricks_judge_client(profile: str = "dbai-dev") -> JudgeClient:
    """Create the Databricks judge client using an explicit workspace profile."""
    from databricks.sdk import WorkspaceClient
    from databricks_openai import DatabricksOpenAI

    return DatabricksOpenAI(workspace_client=WorkspaceClient(profile=profile))


def _http_invoker(url: str, headers: dict[str, str], retries: int = 2) -> AgentInvoker:
    """Create a retrying App invoker for transient HTTP failures."""
    def invoke(request: dict[str, Any]) -> Any:
        payload = json.dumps(request).encode("utf-8")
        for attempt in range(retries + 1):
            http_request = Request(
                url,
                data=payload,
                headers=headers,
                method="POST",
            )
            try:
                with urlopen(http_request, timeout=60) as response:
                    return json.loads(response.read())
            except HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if not retryable or attempt == retries:
                    raise
            except URLError:
                if attempt == retries:
                    raise
            time.sleep(2**attempt)
        raise RuntimeError("App invocation exhausted retries")

    return invoke


def default_agent_invoker(url: str, token: str) -> AgentInvoker:
    """Create an App invoker using an explicitly supplied bearer token."""
    return _http_invoker(_agent_invocation_url(url), {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })


def profile_agent_invoker(url: str, profile: str | None = "dbai-dev") -> AgentInvoker:
    """Create an App invoker using profile or workspace default authentication."""
    from databricks.sdk import WorkspaceClient

    client = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    headers = client.config.authenticate()
    headers["Content-Type"] = "application/json"
    return _http_invoker(_agent_invocation_url(url), headers)
