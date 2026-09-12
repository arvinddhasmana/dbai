"""Adapters for invoking deployed MLflow AgentServer Apps."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from common_utils.evaluation.models import AgentObservation, EvaluationCase


class AgentInvoker(Protocol):
    def __call__(self, request: dict[str, Any]) -> Any: ...


def _invocation_url(url: str) -> str:
    normalized = url.rstrip("/")
    if normalized.endswith("/invocations"):
        return normalized
    return f"{normalized}/api/invocations"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    raise ValueError("Agent response must be a mapping or model with model_dump()")


def extract_answer(response: Mapping[str, Any]) -> str:
    """Extract the final assistant text from a Responses API response."""
    outputs = response.get("output", [])
    for item in reversed(outputs if isinstance(outputs, list) else []):
        if not isinstance(item, Mapping) or item.get("role") not in (None, "assistant"):
            continue
        if item.get("text"):
            return str(item["text"])
        content = item.get("content", [])
        for part in reversed(content if isinstance(content, list) else []):
            if isinstance(part, Mapping) and part.get("text"):
                return str(part["text"])
    output_text = response.get("output_text")
    if output_text:
        return str(output_text)
    raise ValueError("Agent response did not contain assistant text")


def observation_from_response(response: Mapping[str, Any]) -> AgentObservation:
    custom_outputs = response.get("custom_outputs") or {}
    if not isinstance(custom_outputs, Mapping):
        custom_outputs = {}
    evaluation = custom_outputs.get("evaluation") or {}
    if not isinstance(evaluation, Mapping):
        evaluation = {}
    tool_calls = evaluation.get("tool_calls") or []
    normalized_calls = tuple(
        {
            "family": str(call.get("family", "managed_mcp")),
            "name": str(call.get("name", "unknown")),
        }
        for call in tool_calls
        if isinstance(call, Mapping)
    )
    tool_families = tuple(
        sorted(
            {
                str(family)
                for family in (evaluation.get("tool_families") or [])
                if family
            }
        )
    )
    unavailable = custom_outputs.get("unavailable_tools") or []
    return AgentObservation(
        answer=extract_answer(response),
        tool_families=tool_families,
        tool_calls=normalized_calls,
        errors=tuple(str(error) for error in custom_outputs.get("errors", []) or []),
        sources=tuple(
            dict(source) for source in custom_outputs.get("sources", []) or [] if isinstance(source, Mapping)
        ),
        metadata={
            "unavailable_tools": [str(tool) for tool in unavailable],
            "history_backend": custom_outputs.get("history_backend"),
        },
    )


@dataclass(frozen=True)
class SupervisorAdapter:
    """Translate a supervisor App response into the shared observation model."""

    invoker: AgentInvoker

    def invoke(self, case: EvaluationCase) -> AgentObservation:
        response = _as_dict(
            self.invoker({"input": [{"role": "user", "content": case.question}]})
        )
        return observation_from_response(response)


def _http_invoker(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float = 90.0,
    retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
) -> AgentInvoker:
    def invoke(request: dict[str, Any]) -> Any:
        payload = json.dumps(request).encode("utf-8")
        for attempt in range(retries + 1):
            http_request = Request(
                _invocation_url(url),
                data=payload,
                headers=headers,
                method="POST",
            )
            try:
                with urlopen(http_request, timeout=timeout_seconds) as response:
                    return json.loads(response.read())
            except HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if not retryable or attempt == retries:
                    raise RuntimeError(f"Agent invocation failed with HTTP {error.code}") from error
            except URLError as error:
                if attempt == retries:
                    raise RuntimeError("Agent invocation failed to connect") from error
            sleep(2**attempt)
        raise RuntimeError("Agent invocation exhausted retries")

    return invoke


def _runtime_credentials_provider() -> tuple[str, Callable[[], dict[str, str]]] | None:
    try:
        from databricks.sdk.runtime import init_runtime_legacy_auth, init_runtime_repl_auth
    except ImportError:
        return None
    for initialize in (init_runtime_repl_auth, init_runtime_legacy_auth):
        try:
            host, provider = initialize()
        except Exception:
            continue
        if host and provider:
            return host, provider
    return None


def _runtime_app_headers(
    runtime_auth: tuple[str, Callable[[], dict[str, str]]],
    app_name: str,
) -> dict[str, str]:
    workspace_url, provider = runtime_auth
    notebook_headers = dict(provider())
    app_request = Request(
        f"{workspace_url.rstrip('/')}/api/2.0/apps/{quote(app_name, safe='')}",
        headers=notebook_headers,
        method="GET",
    )
    with urlopen(app_request, timeout=30.0) as response:
        app = json.loads(response.read())
    app_client_id = app.get("oauth2_app_client_id")
    if not app_client_id:
        raise RuntimeError(f"App {app_name} has no OAuth client ID")

    subject_token = notebook_headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not subject_token:
        raise RuntimeError("Databricks runtime did not provide a notebook token")
    token_request = Request(
        f"{workspace_url.rstrip('/')}/oidc/v1/token",
        data=urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": subject_token,
                "subject_token_type": "urn:databricks:params:oauth:token-type:personal-access-token",
                "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "scope": "all-apis",
                "audience": app_client_id,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(token_request, timeout=30.0) as response:
            token = json.loads(response.read()).get("access_token")
    except HTTPError as error:
        try:
            error_body = json.loads(error.read(4096).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            error_body = {}
        details = {
            key: str(error_body[key])[:200]
            for key in ("error", "error_description", "message", "detail")
            if error_body.get(key)
        }
        suffix = f": {details}" if details else ""
        raise RuntimeError(
            f"Databricks App token exchange failed with HTTP {error.code}{suffix}"
        ) from error
    if not token:
        raise RuntimeError("Databricks App token exchange returned no access token")
    return {"Authorization": f"Bearer {token}"}


def profile_agent_invoker(
    url: str,
    profile: str | None = None,
    app_name: str | None = None,
) -> AgentInvoker:
    """Create an OAuth/profile-authenticated invoker for a Databricks App."""
    from databricks.sdk import WorkspaceClient

    runtime_auth = None if profile else _runtime_credentials_provider()
    if runtime_auth:
        if not app_name:
            raise ValueError("Managed Job App invocation requires an app name")
        headers = _runtime_app_headers(runtime_auth, app_name)
    else:
        client = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
        headers = dict(client.config.authenticate())
    headers["Content-Type"] = "application/json"
    return _http_invoker(url, headers)


def token_agent_invoker(url: str, token: str) -> AgentInvoker:
    """Create a bearer-token invoker for local diagnostics only."""
    return _http_invoker(
        url,
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )