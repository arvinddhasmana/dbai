import asyncio
from decimal import Decimal

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agent_server.config import Settings
from agent_server.models import (
    Citation,
    JiraIssue,
    PenaltyEvidence,
    SpecialistResult,
    VendorPolicy,
)
from agent_server.workflow import (
    BossRuntime,
    build_graph,
    decide_manager_approval,
    handle_message,
    manager_approval_details,
)


ANSWER = (
    "Penalty applies to VEND-1. The penalty is USD 1250. "
    "The contract states: Late delivery incurs a penalty of USD 1250."
)


class FakeSpecialist:
    async def ask(self, question, conversation_id):
        return SpecialistResult(answer=ANSWER)


class FakeExtractor:
    async def extract(self, question, specialist):
        return PenaltyEvidence(
            penalty_applicable=True,
            vendor_id="VEND-1",
            vendor_name="Northwind",
            amount=Decimal("1250"),
            currency="USD",
            contract_basis="Late delivery incurs a penalty of USD 1250.",
            amount_quote="penalty of USD 1250",
            citations=[
                Citation(
                    source="contract.pdf, chunk 3",
                    quote="Late delivery incurs a penalty of USD 1250.",
                )
            ],
        )


class FakePolicyLookup:
    def __init__(self, support_tier="Gold"):
        self.support_tier = support_tier

    async def lookup(self, vendor_id):
        return VendorPolicy(
            vendor_id=vendor_id,
            vendor_name="Northwind",
            support_tier=self.support_tier,
        )


class FakeJira:
    def __init__(self):
        self.created = []
        self.comments = []

    async def create_issue(
        self, issue_type_name, summary, description, assignee_email
    ):
        key = f"KAN-{len(self.created) + 1}"
        issue = JiraIssue(
            key=key,
            url=f"https://jira.example.com/browse/{key}",
        )
        self.created.append(
            {
                "issue_type_name": issue_type_name,
                "summary": summary,
                "description": description,
                "assignee_email": assignee_email,
                "issue": issue,
            }
        )
        return issue

    async def add_comment(self, issue_key, comment):
        self.comments.append((issue_key, comment))


def create_runtime(support_tier="Gold"):
    settings = Settings(
        specialist_app_name="specialist",
        model_endpoint="test-model",
        jira_project_key="KAN",
        jira_approval_issue_type_name="Request",
        jira_penalty_issue_type_name="VendorPenalty",
        jira_approver_email="approver@example.com",
        jira_penalty_assignee_email="penalty-owner@example.com",
        jira_base_url="https://jira.example.com",
        sql_warehouse_id="warehouse",
        jira_mcp_url="https://workspace.example.com/ai-gateway/mcp-services/system.ai.atlassian",
        lakebase_branch="",
        lakebase_endpoint=None,
        app_base_url="https://boss.example.com",
        checkpoint_backend="memory",
    )
    specialist = FakeSpecialist()
    extractor = FakeExtractor()
    policy = FakePolicyLookup(support_tier)
    jira = FakeJira()
    graph = build_graph(
        settings, specialist, extractor, policy, jira, MemorySaver()
    )
    return BossRuntime(graph, settings, specialist, extractor, policy, jira), jira


def run(coro):
    return asyncio.run(coro)


def send_request(runtime, conversation_id, message):
    return run(
        handle_message(
            runtime,
            conversation_id,
            message,
            "user@example.com",
            "https://boss.example.com",
        )
    )


def test_informational_question_does_not_create_jira_item():
    runtime, jira = create_runtime()

    result = send_request(
        runtime,
        "conversation-1",
        "What does the contract say about delivery penalties?",
    )

    assert result["status"] == "answered"
    assert result["response"] == ANSWER
    assert jira.created == []


def test_non_platinum_requires_exact_user_confirmation_then_creates_one_item():
    runtime, jira = create_runtime()

    pending = send_request(
        runtime, "conversation-2", "Create a penalty tracking item for vendor VEND-1"
    )
    assert pending["status"] == "awaiting_user_confirmation"
    assert jira.created == []

    ask_again = send_request(runtime, "conversation-2", "sounds good")
    assert ask_again["status"] == "awaiting_user_confirmation"
    assert jira.created == []

    completed = send_request(runtime, "conversation-2", "yes")
    assert completed["status"] == "penalty_item_created"
    assert len(jira.created) == 1
    assert jira.created[0]["issue_type_name"] == "VendorPenalty"
    assert jira.created[0]["assignee_email"] == "penalty-owner@example.com"
    assert completed["jira_items"][0]["key"] == "KAN-1"
    assert completed["jira_items"][0]["kind"] == "penalty_tracking"
    assert "does not notify or charge the vendor" in jira.created[0]["description"]


def test_platinum_rejection_records_approval_and_creates_no_penalty_item():
    runtime, jira = create_runtime(support_tier="Platinum")

    pending = send_request(
        runtime, "conversation-3", "Create a penalty tracking item for vendor VEND-1"
    )
    assert pending["status"] == "awaiting_user_confirmation"
    assert jira.created == []

    manager_pending = send_request(runtime, "conversation-3", "yes")
    assert manager_pending["status"] == "approval_pending"
    assert manager_pending["approval_url"].startswith(
        "https://boss.example.com/approval/"
    )
    assert manager_pending["approval_issue_url"] == (
        "https://jira.example.com/browse/KAN-1"
    )
    assert len(jira.created) == 1
    assert jira.created[0]["issue_type_name"] == "Request"
    assert jira.created[0]["assignee_email"] == "approver@example.com"
    assert manager_pending["jira_items"][0]["kind"] == "approval"

    details = run(
        manager_approval_details(runtime, manager_pending["conversation_id"])
    )
    assert details["issue_key"] == "KAN-1"

    rejected = run(
        decide_manager_approval(
            runtime, manager_pending["conversation_id"], approved=False
        )
    )
    assert rejected["status"] == "manager_rejected"
    assert len(jira.created) == 1
    assert jira.comments == [
        ("KAN-1", "Approval decision recorded in the Boss App: rejected.")
    ]
    assert rejected["jira_items"][0]["status"] == "rejected"


def test_platinum_approval_creates_second_penalty_item_and_rejects_duplicate():
    runtime, jira = create_runtime(support_tier="Platinum")

    send_request(
        runtime, "conversation-4", "Create a penalty tracking item for vendor VEND-1"
    )
    manager_pending = send_request(runtime, "conversation-4", "yes")

    approved = run(
        decide_manager_approval(
            runtime, manager_pending["conversation_id"], approved=True
        )
    )
    assert approved["status"] == "penalty_item_created"
    assert [item["issue_type_name"] for item in jira.created] == [
        "Request",
        "VendorPenalty",
    ]
    assert [item["assignee_email"] for item in jira.created] == [
        "approver@example.com",
        "penalty-owner@example.com",
    ]
    assert "Approved Jira request: KAN-1" in jira.created[1]["description"]
    assert [item["key"] for item in approved["jira_items"]] == ["KAN-1", "KAN-2"]
    assert jira.comments == [
        ("KAN-1", "Approval decision recorded in the Boss App: approved.")
    ]

    with pytest.raises(LookupError):
        run(
            decide_manager_approval(
                runtime, manager_pending["conversation_id"], approved=True
            )
        )
    assert len(jira.created) == 2


def test_missing_vendor_email_is_not_required_for_jira_workflow():
    runtime, jira = create_runtime()

    send_request(
        runtime, "conversation-5", "Create a penalty tracking item for vendor VEND-1"
    )
    result = send_request(runtime, "conversation-5", "yes")

    assert result["status"] == "penalty_item_created"
    assert len(jira.created) == 1


def test_user_cancellation_never_creates_jira_item():
    runtime, jira = create_runtime(support_tier="Platinum")

    send_request(
        runtime, "conversation-6", "Create a penalty tracking item for vendor VEND-1"
    )
    cancelled = send_request(runtime, "conversation-6", "no")

    assert cancelled["status"] == "user_rejected"
    assert jira.created == []


def test_platinum_rejects_unknown_or_stale_approval_decisions():
    runtime, jira = create_runtime(support_tier="Platinum")

    with pytest.raises(LookupError):
        run(decide_manager_approval(runtime, "0" * 64, approved=True))
    assert jira.created == []
