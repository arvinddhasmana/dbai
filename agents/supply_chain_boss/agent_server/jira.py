"""Narrow adapter for Jira work items through the governed Atlassian MCP Service.

Only the two required Jira operations are exposed here. Tool names, argument
schemas, success payloads, account matches, and issue identifiers are checked
before their results are trusted; calls use the requesting user's token.
"""

import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from databricks_langchain import DatabricksMCPServer, DatabricksMultiServerMCPClient

from agent_server.auth import current_user_access_token
from agent_server.config import Settings
from agent_server.models import JiraIssue

logger = logging.getLogger(__name__)


class JiraMcpClient:
    """Create internal tracking issues and comments without exposing raw MCP tools."""

    def __init__(
        self,
        settings: Settings,
        workspace_client: Any,
        tools: list[Any] | None = None,
    ) -> None:
        """Configure the client; injected tools keep integration tests deterministic."""
        self._settings = settings
        self._workspace_client = workspace_client
        self._tools = tools

    async def create_issue(
        self,
        issue_type_name: str,
        summary: str,
        description: str,
        assignee_email: str,
    ) -> JiraIssue:
        """Resolve the assignee and create one Jira issue of the requested type."""
        normalized_summary = self._validated_summary(summary)
        base_url = self._jira_base_url()
        account_id = await self._account_id(assignee_email)
        result = await self._call(
            "createJiraIssue",
            {
                "projectKey": self._settings.jira_project_key,
                "issueTypeName": issue_type_name,
                "summary": normalized_summary,
                "description": description,
                "assignee_account_id": account_id,
                "contentFormat": "markdown",
            },
        )
        payload = self._json_payload(result)
        key = payload.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", key):
            raise RuntimeError("Jira createJiraIssue returned no valid issue key.")
        return JiraIssue(
            key=key,
            url=f"{base_url}/browse/{key}",
        )

    async def add_comment(self, issue_key: str, comment: str) -> None:
        """Record an approval result on a validated Jira issue key."""
        if not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", issue_key):
            raise ValueError("Invalid Jira issue key.")
        result = await self._call(
            "addCommentToJiraIssue",
            {
                "issueIdOrKey": issue_key,
                "commentBody": comment,
                "contentFormat": "markdown",
            },
        )
        self._ensure_success(result, "addCommentToJiraIssue")

    async def _account_id(self, email: str) -> str:
        """Require an unambiguous account match before assigning a Jira issue."""
        address = email.strip()
        if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", address):
            raise RuntimeError("A configured Jira assignee email is invalid.")
        result = await self._call(
            "lookupJiraAccountId",
            {"searchString": address},
        )
        payload = self._json_payload(result)
        accounts = self._account_candidates(payload)
        matches = [
            account
            for account in accounts
            if account.get("emailAddress", "").casefold() == address.casefold()
        ]
        if not matches and len(accounts) == 1:
            matches = accounts
        if len(matches) != 1:
            raise RuntimeError(
                "Jira account lookup did not resolve the configured email "
                "to exactly one account."
            )
        account_id = matches[0].get("accountId")
        if not isinstance(account_id, str) or not account_id:
            raise RuntimeError("Jira account lookup returned no account ID.")
        return account_id

    @staticmethod
    def _account_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract candidate account records from supported MCP response shapes."""
        data = payload.get("data")
        if isinstance(data, dict):
            users = data.get("users")
            if isinstance(users, dict) and isinstance(users.get("users"), list):
                return [
                    item for item in users["users"] if isinstance(item, dict)
                ]
        for key in ("users", "values", "results", "accounts"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if isinstance(payload.get("accountId"), str):
            return [payload]
        return []

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke exactly one matching tool after validating its advertised schema."""
        tools = await self._get_tools()
        matches = [tool for tool in tools if getattr(tool, "name", None) == name]
        if len(matches) != 1:
            raise RuntimeError(
                f"Atlassian MCP tool {name!r} was not available unambiguously."
            )
        tool = matches[0]
        self._validate_arguments(tool, arguments)
        result = await tool.ainvoke(arguments)
        self._ensure_success(result, name)
        return result

    async def _get_tools(self) -> list[Any]:
        """Return injected test tools or discover MCP tools with the user's token."""
        if self._tools is not None:
            return self._tools
        token = current_user_access_token()
        if not token:
            raise RuntimeError(
                "The authenticated user's Databricks token is required to call "
                "the system.ai.atlassian MCP Service."
            )
        from databricks.sdk import WorkspaceClient

        user_workspace_client = WorkspaceClient(token=token, auth_type="pat")
        service_url = self._settings.jira_mcp_url or (
            f"{self._workspace_client.config.host.rstrip('/')}"
            "/ai-gateway/mcp-services/system.ai.atlassian"
        )
        client = DatabricksMultiServerMCPClient(
            [
                DatabricksMCPServer(
                    name="system-ai-atlassian",
                    url=service_url,
                    workspace_client=user_workspace_client,
                )
            ]
        )
        return await client.get_tools()

    @staticmethod
    def _validate_arguments(tool: Any, arguments: dict[str, Any]) -> None:
        """Reject unsupported or missing fields instead of guessing tool inputs."""
        schema = getattr(tool, "args_schema", None)
        if isinstance(schema, dict):
            schema_json = schema
        elif hasattr(schema, "model_json_schema"):
            schema_json = schema.model_json_schema()
        else:
            raise RuntimeError(
                f"Atlassian MCP tool {getattr(tool, 'name', '')!r} "
                "has no inspectable argument schema."
            )
        required = schema_json.get("required", [])
        properties = schema_json.get("properties", {})
        missing = [
            field
            for field in required
            if field not in arguments and field in properties
        ]
        unknown = [field for field in arguments if field not in properties]
        if missing or unknown:
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unknown:
                details.append("unsupported " + ", ".join(unknown))
            raise RuntimeError(
                f"Atlassian MCP tool {getattr(tool, 'name', '')!r} schema "
                f"mismatch: {'; '.join(details)}."
            )

    @classmethod
    def _ensure_success(cls, result: Any, tool_name: str) -> None:
        """Raise on MCP-level and embedded error responses, regardless of shape."""
        if isinstance(result, dict) and result.get("isError") is True:
            raise RuntimeError(f"Atlassian MCP tool {tool_name} returned an error.")
        if getattr(result, "isError", False) is True:
            raise RuntimeError(f"Atlassian MCP tool {tool_name} returned an error.")
        for item in cls._result_items(result):
            if isinstance(item, dict) and item.get("isError") is True:
                raise RuntimeError(f"Atlassian MCP tool {tool_name} returned an error.")
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    payload = json.loads(item.get("text", ""))
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict) and (
                    payload.get("error") or payload.get("isError") is True
                ):
                    raise RuntimeError(
                        f"Atlassian MCP tool {tool_name} returned an error."
                    )
                status = payload.get("statusCode") if isinstance(payload, dict) else None
                if isinstance(status, int) and status >= 400:
                    raise RuntimeError(
                        f"Atlassian MCP tool {tool_name} returned HTTP {status}."
                    )

    @staticmethod
    def _result_items(result: Any) -> list[Any]:
        """Normalize content items from dictionary and SDK result objects."""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            content = result.get("content")
            return content if isinstance(content, list) else [result]
        content = getattr(result, "content", None)
        return content if isinstance(content, list) else [result]

    @classmethod
    def _json_payload(cls, result: Any) -> dict[str, Any]:
        """Parse one structured JSON payload from MCP text content."""
        for item in cls._result_items(result):
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    payload = json.loads(item["text"])
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    return payload
                raise RuntimeError("Atlassian MCP returned an unexpected JSON shape.")
        if isinstance(result, dict) and isinstance(result.get("key"), str):
            return result
        raise RuntimeError("Atlassian MCP returned no JSON result.")

    @staticmethod
    def _validated_summary(summary: str) -> str:
        """Reject blank or oversized issue summaries before sending them to Jira."""
        normalized = " ".join(summary.split()).strip()
        if not normalized or len(normalized) > 255:
            raise ValueError("Jira issue summary must contain 1 to 255 characters.")
        return normalized

    def _jira_base_url(self) -> str:
        """Require a clean HTTPS Jira origin before constructing issue links."""
        candidate = self._settings.jira_base_url.rstrip("/")
        parsed = urlsplit(candidate)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("JIRA_BASE_URL must be an HTTPS site origin.")
        return candidate
