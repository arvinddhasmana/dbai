import asyncio
import json

import pytest

from agent_server.config import Settings
from agent_server.jira import JiraMcpClient


def settings():
    return Settings(
        specialist_app_name="specialist",
        model_endpoint="model",
        jira_project_key="KAN",
        jira_approval_issue_type_name="Request",
        jira_penalty_issue_type_name="VendorPenalty",
        jira_approver_email="approver@example.com",
        jira_penalty_assignee_email="assignee@example.com",
        jira_base_url="https://example.atlassian.net",
        sql_warehouse_id="warehouse",
        jira_mcp_url="https://workspace.example.com/ai-gateway/mcp-services/system.ai.atlassian",
        lakebase_branch="",
        lakebase_endpoint=None,
        app_base_url="https://boss.example.com",
        checkpoint_backend="memory",
    )


class FakeTool:
    def __init__(self, name, required, result, properties=None):
        self.name = name
        self.args_schema = {
            "type": "object",
            "properties": {
                field: {"type": "string"}
                for field in (properties or required)
            },
            "required": required,
        }
        self.result = result
        self.calls = []

    async def ainvoke(self, arguments):
        self.calls.append(arguments)
        return self.result


def result(payload):
    return [{"type": "text", "text": json.dumps(payload)}]


def run(coro):
    return asyncio.run(coro)


def make_client(create_result=None, lookup_result=None):
    lookup = FakeTool(
        "lookupJiraAccountId",
        ["searchString"],
        result(
            lookup_result
            or {
                "users": [
                    {
                        "accountId": "account-123",
                        "emailAddress": "assignee@example.com",
                    }
                ]
            }
        ),
    )
    create = FakeTool(
        "createJiraIssue",
        ["projectKey", "issueTypeName", "summary"],
        result(create_result or {"id": "10001", "key": "KAN-41"}),
        [
            "projectKey",
            "issueTypeName",
            "summary",
            "description",
            "assignee_account_id",
            "contentFormat",
        ],
    )
    comment = FakeTool(
        "addCommentToJiraIssue",
        ["issueIdOrKey", "commentBody"],
        result({"id": "comment-1"}),
        ["issueIdOrKey", "commentBody", "contentFormat"],
    )
    return JiraMcpClient(
        settings(),
        workspace_client=object(),
        tools=[lookup, create, comment],
    ), lookup, create, comment


def test_issue_creation_resolves_assignee_and_returns_browse_link():
    client, lookup, create, _ = make_client()

    issue = run(
        client.create_issue(
            "VendorPenalty",
            "Penalty tracking: Northwind",
            "Internal tracking only.",
            "assignee@example.com",
        )
    )

    assert issue.key == "KAN-41"
    assert issue.url == "https://example.atlassian.net/browse/KAN-41"
    assert lookup.calls == [{"searchString": "assignee@example.com"}]
    assert create.calls == [
        {
            "projectKey": "KAN",
            "issueTypeName": "VendorPenalty",
            "summary": "Penalty tracking: Northwind",
            "description": "Internal tracking only.",
            "assignee_account_id": "account-123",
            "contentFormat": "markdown",
        }
    ]


def test_account_lookup_fails_closed_when_results_are_ambiguous():
    client, _, _, _ = make_client(
        lookup_result={
            "users": [
                {"accountId": "one", "emailAddress": "other@example.com"},
                {"accountId": "two", "emailAddress": "other@example.com"},
            ]
        }
    )
    with pytest.raises(RuntimeError, match="exactly one account"):
        run(
            client.create_issue(
                "VendorPenalty",
                "Penalty tracking",
                "Details",
                "assignee@example.com",
            )
        )


def test_nested_account_lookup_response_is_supported():
    client, _, create, _ = make_client(
        lookup_result={
            "data": {
                "users": {
                    "users": [{"accountId": "account-123", "displayName": "Assignee"}],
                    "total": 1,
                }
            },
            "statusCode": 200,
        }
    )

    run(
        client.create_issue(
            "VendorPenalty",
            "Penalty tracking",
            "Details",
            "assignee@example.com",
        )
    )

    assert create.calls[0]["assignee_account_id"] == "account-123"


def test_issue_creation_rejects_malformed_tool_result():
    client, _, _, _ = make_client(create_result={"id": "10001"})

    with pytest.raises(RuntimeError, match="no valid issue key"):
        run(
            client.create_issue(
                "VendorPenalty",
                "Penalty tracking",
                "Details",
                "assignee@example.com",
            )
        )


def test_summary_is_trimmed_and_limited():
    client, _, create, _ = make_client()

    run(
        client.create_issue(
            "Request",
            "  Approval\nrequired  ",
            "Details",
            "assignee@example.com",
        )
    )
    assert create.calls[0]["summary"] == "Approval required"

    with pytest.raises(ValueError, match="1 to 255"):
        run(
            client.create_issue(
                "Request",
                "x" * 256,
                "Details",
                "assignee@example.com",
            )
        )


def test_comment_uses_expected_jira_tool_arguments():
    client, _, _, comment = make_client()

    run(client.add_comment("KAN-41", "Approval decision: approved."))

    assert comment.calls == [
        {
            "issueIdOrKey": "KAN-41",
            "commentBody": "Approval decision: approved.",
            "contentFormat": "markdown",
        }
    ]


def test_missing_tool_fails_explicitly():
    client = JiraMcpClient(settings(), workspace_client=object(), tools=[])

    with pytest.raises(RuntimeError, match="lookupJiraAccountId"):
        run(
            client.create_issue(
                "VendorPenalty",
                "Penalty tracking",
                "Details",
                "assignee@example.com",
            )
        )
