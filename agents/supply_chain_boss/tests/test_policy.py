from types import SimpleNamespace

import pytest

from agent_server.config import Settings
from agent_server.policy import VendorPolicyLookup


def make_settings(approval_required_support_tiers):
    return Settings(
        specialist_app_name="specialist",
        model_endpoint="model",
        approval_required_support_tiers=frozenset(
            tier.casefold() for tier in approval_required_support_tiers
        ),
        jira_project_key="KAN",
        jira_approval_issue_type_name="Request",
        jira_penalty_issue_type_name="VendorPenalty",
        jira_approver_email="approver@example.com",
        jira_penalty_assignee_email="assignee@example.com",
        jira_base_url="https://example.atlassian.net",
        sql_warehouse_id="warehouse",
        jira_mcp_url="",
        lakebase_branch="",
        lakebase_endpoint=None,
        checkpoint_backend="memory",
    )


@pytest.mark.parametrize(
    ("support_tier", "approval_tiers", "approval_required"),
    [
        ("Gold", ("Gold", "Platinum"), True),
        ("PLATINUM", ("Gold", "Platinum"), True),
        ("Silver", ("Gold", "Platinum"), False),
        (" Enterprise ", ("Enterprise",), True),
    ],
)
def test_vendor_policy_uses_configured_approval_tiers(
    support_tier, approval_tiers, approval_required
):
    columns = [
        SimpleNamespace(name="vendor_id"),
        SimpleNamespace(name="vendor_name"),
        SimpleNamespace(name="support_tier"),
    ]
    response = SimpleNamespace(
        status=SimpleNamespace(state="SUCCEEDED", error=None),
        manifest=SimpleNamespace(schema=SimpleNamespace(columns=columns)),
        result=SimpleNamespace(
            data_array=[["VEND-1", "Northwind", support_tier]]
        ),
    )
    workspace_client = SimpleNamespace(
        statement_execution=SimpleNamespace(
            execute_statement=lambda **kwargs: response
        )
    )

    policy = VendorPolicyLookup(
        make_settings(approval_tiers), workspace_client
    )._lookup_sync("VEND-1")

    assert policy.approval_required is approval_required
