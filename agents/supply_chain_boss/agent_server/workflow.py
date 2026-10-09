"""LangGraph workflow and resumable approval handling."""

from __future__ import annotations

import hashlib
import re
from contextlib import AsyncExitStack
from typing import Any, TypedDict
from urllib.parse import quote

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg_pool import AsyncConnectionPool

from agent_server.auth import use_user_access_token
from agent_server.config import Settings
from agent_server.jira import JiraMcpClient
from agent_server.models import (
    JiraIssue,
    PenaltyEvidence,
    PenaltyProposal,
    SpecialistResult,
    VendorPolicy,
)
from agent_server.policy import VendorPolicyLookup
from agent_server.specialist import PenaltyEvidenceExtractor, SpecialistClient


class BossState(TypedDict, total=False):
    question: str
    app_base_url: str | None
    thread_id: str
    specialist: dict[str, Any]
    evidence: dict[str, Any]
    vendor_policy: dict[str, Any]
    proposal: dict[str, Any]
    status: str
    response: str
    pending_approval: dict[str, Any]
    approval_decision: bool
    jira_items: list[dict[str, str]]


class BossRuntime:
    def __init__(
        self,
        graph: Any,
        settings: Settings,
        specialist: SpecialistClient,
        extractor: PenaltyEvidenceExtractor,
        policy_lookup: VendorPolicyLookup,
        jira_client: JiraMcpClient,
        resource_stack: AsyncExitStack | None = None,
    ) -> None:
        self.graph = graph
        self.settings = settings
        self.specialist = specialist
        self.extractor = extractor
        self.policy_lookup = policy_lookup
        self.jira_client = jira_client
        self._resource_stack = resource_stack

    async def aclose(self) -> None:
        resource_stack, self._resource_stack = self._resource_stack, None
        if resource_stack is not None:
            await resource_stack.aclose()


def _action_request(question: str) -> bool:
    return bool(
        re.search(
            r"\b(apply|assess|charge|create|deduct|impose|issue|levy|send|notify)\b.{0,80}"
            r"\b(penalt(?:y|ies)|fee|liquidated damages)\b"
            r"|\b(penalt(?:y|ies)|fee|liquidated damages)\b.{0,80}"
            r"\b(apply|assess|charge|create|deduct|impose|issue|levy|send|notify)\b",
            question,
            re.IGNORECASE,
        )
    )


def _proposal_summary(proposal: PenaltyProposal) -> str:
    return (
        f"Vendor: {proposal.vendor_name} ({proposal.vendor_id})\n"
        f"Penalty: {proposal.currency} {proposal.amount}\n"
        f"Contract basis: {proposal.contract_basis}\n"
        f"Support tier: {proposal.support_tier}\n"
        f"Manager approval required: {'Yes' if proposal.approval_required else 'No'}"
    )


def _jira_issue_result(issue: JiraIssue, kind: str, status: str) -> dict[str, str]:
    return {
        "kind": kind,
        "key": issue.key,
        "url": issue.url,
        "status": status,
    }


def build_graph(
    settings: Settings,
    specialist: SpecialistClient,
    extractor: PenaltyEvidenceExtractor,
    policy_lookup: VendorPolicyLookup,
    jira_client: JiraMcpClient,
    checkpointer: Any,
) -> Any:
    async def analyze(state: BossState) -> BossState:
        result = await specialist.ask(state["question"], state["thread_id"])
        return {
            "specialist": result.model_dump(mode="json"),
            "response": result.answer,
            "status": "answered",
        }

    async def extract_evidence(state: BossState) -> BossState:
        result = SpecialistResult.model_validate(state["specialist"])
        evidence = await extractor.extract(state["question"], result)
        if evidence.penalty_applicable is False:
            return {
                "evidence": evidence.model_dump(mode="json"),
                "status": "not_applicable",
                "response": (
                    "The specialist evidence does not support a penalty action. "
                    + result.answer
                ),
            }
        missing = []
        if evidence.penalty_applicable is not True:
            missing.append("whether the penalty applies")
        if not evidence.vendor_id:
            missing.append("a vendor identifier")
        if not evidence.vendor_name:
            missing.append("a vendor name")
        if evidence.amount is None or not evidence.currency:
            missing.append("an explicit amount and currency")
        elif not re.fullmatch(r"[A-Za-z]{3}", evidence.currency):
            missing.append("a valid three-letter currency code")
        if not evidence.contract_basis or not evidence.citations:
            missing.append("verifiable contract evidence and citations")
        if missing:
            return {
                "evidence": evidence.model_dump(mode="json"),
                "status": "blocked",
                "response": (
                    "I cannot safely prepare a penalty action because the "
                    "specialist evidence is missing "
                    + ", ".join(missing)
                    + ". No Jira item was created."
                ),
            }
        return {
            "evidence": evidence.model_dump(mode="json"),
            "status": "evidence_ready",
        }

    async def lookup_policy(state: BossState) -> BossState:
        evidence = PenaltyEvidence.model_validate(state["evidence"])
        if not evidence.vendor_id or not evidence.amount or not evidence.currency:
            return {
                "status": "blocked",
                "response": "The penalty evidence is incomplete. No Jira item was created.",
            }
        vendor = await policy_lookup.lookup(evidence.vendor_id)
        if vendor.vendor_id.casefold() != evidence.vendor_id.casefold():
            raise ValueError("Vendor policy lookup returned a different vendor.")
        proposal = PenaltyProposal(
            vendor_id=evidence.vendor_id,
            vendor_name=evidence.vendor_name or vendor.vendor_name or evidence.vendor_id,
            amount=evidence.amount,
            currency=evidence.currency.upper(),
            contract_basis=evidence.contract_basis or "",
            citations=evidence.citations,
            support_tier=vendor.support_tier,
            approval_required=vendor.approval_required,
        )
        return {
            "vendor_policy": vendor.model_dump(mode="json"),
            "proposal": proposal.model_dump(mode="json"),
            "status": "awaiting_user_confirmation",
        }

    async def await_user_confirmation(state: BossState) -> BossState:
        proposal = PenaltyProposal.model_validate(state["proposal"])
        decision = interrupt(
            {
                "kind": "user_confirmation",
                "prompt": (
                    "Please confirm this exact penalty action by replying "
                    "'yes' or 'no':\n" + _proposal_summary(proposal)
                ),
                "proposal": proposal.model_dump(mode="json"),
            }
        )
        if not isinstance(decision, dict) or decision.get("approved") not in {
            True,
            False,
        }:
            raise ValueError("User confirmation must be an explicit yes or no.")
        if not decision["approved"]:
            return {
                "status": "user_rejected",
                "response": "Penalty action cancelled. No Jira item was created.",
            }
        return {"status": "user_confirmed"}

    async def create_approval_item(state: BossState) -> BossState:
        proposal = PenaltyProposal.model_validate(state["proposal"])
        base_url = (state.get("app_base_url") or "").rstrip("/")
        if not base_url:
            raise RuntimeError(
                "A trusted incoming App URL is required to create the "
                "manager approval link."
            )
        approval_url = f"{base_url}/approval/{quote(state['thread_id'], safe='')}"
        safe_vendor_name = re.sub(r"[\r\n]+", " ", proposal.vendor_name)[:120]
        summary = f"Approval required: vendor penalty for {safe_vendor_name}"
        description = (
            "A user-confirmed vendor penalty requires manager approval.\n\n"
            f"{_proposal_summary(proposal)}\n\n"
            f"Contract evidence: {proposal.contract_basis}\n\n"
            f"Record the decision in the Boss App: {approval_url}\n\n"
            "This Jira item tracks the approval request. It does not notify or "
            "charge the vendor."
        )
        issue = await jira_client.create_issue(
            settings.jira_approval_issue_type_name,
            summary,
            description,
            settings.jira_approver_email,
        )
        jira_items = list(state.get("jira_items", []))
        jira_items.append(_jira_issue_result(issue, "approval", "pending"))
        return {
            "pending_approval": {
                "request_id": state["thread_id"],
                "status": "pending",
                "issue_key": issue.key,
                "issue_url": issue.url,
                "approval_url": approval_url,
            },
            "status": "approval_pending",
            "response": (
                "Your penalty request is awaiting manager approval. "
                f"Jira approval item {issue.key} was assigned to the configured "
                "approver. Creating the item does not constitute approval."
            ),
            "jira_items": jira_items,
        }

    async def await_manager_decision(state: BossState) -> BossState:
        pending = state.get("pending_approval") or {}
        decision = interrupt(
            {
                "kind": "manager_approval",
                "request_id": pending.get("request_id"),
                "proposal": state["proposal"],
            }
        )
        if (
            not isinstance(decision, dict)
            or decision.get("request_id") != pending.get("request_id")
            or decision.get("approved") not in {True, False}
        ):
            raise ValueError("Manager decision does not match the pending request.")
        issue_key = pending.get("issue_key")
        if not isinstance(issue_key, str):
            raise RuntimeError("The pending Jira approval item has no issue key.")
        decision_name = "approved" if decision["approved"] else "rejected"
        await jira_client.add_comment(
            issue_key,
            f"Approval decision recorded in the Boss App: {decision_name}.",
        )
        jira_items = [
            {
                **item,
                "status": decision_name if item.get("key") == issue_key else item.get("status", ""),
            }
            for item in state.get("jira_items", [])
        ]
        if not decision["approved"]:
            return {
                "approval_decision": False,
                "jira_items": jira_items,
                "pending_approval": {
                    **pending,
                    "status": "rejected",
                    "decision": "rejected",
                },
                "status": "manager_rejected",
                "response": (
                    "The penalty request was rejected. No penalty-tracking "
                    "Jira item was created."
                ),
            }
        return {
            "approval_decision": True,
            "jira_items": jira_items,
            "pending_approval": {
                **pending,
                "status": "approved",
                "decision": "approved",
            },
            "status": "manager_approved",
        }

    async def create_penalty_item(state: BossState) -> BossState:
        proposal = PenaltyProposal.model_validate(state["proposal"])
        if proposal.approval_required and state.get("approval_decision") is not True:
            raise RuntimeError(
                "A vendor in an approval-required support tier requires "
                "recorded manager approval."
            )
        safe_vendor_name = re.sub(r"[\r\n]+", " ", proposal.vendor_name)[:120]
        approval = state.get("pending_approval") or {}
        approval_reference = (
            f"\n\nApproved Jira request: {approval.get('issue_key')} "
            f"({approval.get('issue_url')})"
            if proposal.approval_required
            else ""
        )
        description = (
            "Penalty-tracking work item created after user confirmation.\n\n"
            f"{_proposal_summary(proposal)}\n\n"
            f"Contract evidence: {proposal.contract_basis}"
            f"{approval_reference}\n\n"
            "This Jira item records internal work only; it does not notify or "
            "charge the vendor."
        )
        issue = await jira_client.create_issue(
            settings.jira_penalty_issue_type_name,
            f"Penalty tracking: {safe_vendor_name}",
            description,
            settings.jira_penalty_assignee_email,
        )
        prior_items = list(state.get("jira_items", []))
        prior_items.append(_jira_issue_result(issue, "penalty_tracking", "created"))
        return {
            "jira_items": prior_items,
            "status": "penalty_item_created",
            "response": (
                f"Created Jira penalty-tracking item {issue.key} for "
                f"{proposal.vendor_name}. This records internal work; no vendor "
                "notification was sent and no charge was executed."
            ),
        }

    def after_analysis(state: BossState) -> str:
        return "extract" if _action_request(state["question"]) else "finish"

    def after_extraction(state: BossState) -> str:
        return "lookup" if state.get("status") == "evidence_ready" else "finish"

    def after_policy(state: BossState) -> str:
        return (
            "confirm"
            if state.get("status") == "awaiting_user_confirmation"
            else "finish"
        )

    def after_confirmation(state: BossState) -> str:
        if state.get("status") != "user_confirmed":
            return "finish"
        proposal = PenaltyProposal.model_validate(state["proposal"])
        return "manager" if proposal.approval_required else "penalty"

    def after_manager_decision(state: BossState) -> str:
        return "penalty" if state.get("approval_decision") is True else "finish"

    builder = StateGraph(BossState)
    builder.add_node("analyze", analyze)
    builder.add_node("extract_evidence", extract_evidence)
    builder.add_node("lookup_policy", lookup_policy)
    builder.add_node("await_user_confirmation", await_user_confirmation)
    builder.add_node("create_approval_item", create_approval_item)
    builder.add_node("await_manager_decision", await_manager_decision)
    builder.add_node("create_penalty_item", create_penalty_item)
    builder.add_edge(START, "analyze")
    builder.add_conditional_edges(
        "analyze",
        after_analysis,
        {"extract": "extract_evidence", "finish": END},
    )
    builder.add_conditional_edges(
        "extract_evidence",
        after_extraction,
        {"lookup": "lookup_policy", "finish": END},
    )
    builder.add_conditional_edges(
        "lookup_policy",
        after_policy,
        {"confirm": "await_user_confirmation", "finish": END},
    )
    builder.add_conditional_edges(
        "await_user_confirmation",
        after_confirmation,
        {
            "manager": "create_approval_item",
            "penalty": "create_penalty_item",
            "finish": END,
        },
    )
    builder.add_edge("create_approval_item", "await_manager_decision")
    builder.add_conditional_edges(
        "await_manager_decision",
        after_manager_decision,
        {"penalty": "create_penalty_item", "finish": END},
    )
    builder.add_edge("create_penalty_item", END)
    return builder.compile(checkpointer=checkpointer)


async def create_runtime_async(settings: Settings) -> BossRuntime:
    from databricks.sdk import WorkspaceClient
    from databricks_langchain import AsyncCheckpointSaver

    resource_stack = AsyncExitStack()
    await resource_stack.__aenter__()
    try:
        workspace_client = WorkspaceClient()
        if settings.checkpoint_backend == "memory":
            checkpointer: Any = MemorySaver()
        elif settings.lakebase_endpoint:
            checkpointer = AsyncCheckpointSaver(
                autoscaling_endpoint=settings.lakebase_endpoint,
                workspace_client=workspace_client,
                schema="supply_chain_boss",
                check=AsyncConnectionPool.check_connection,
            )
        elif settings.lakebase_branch:
            checkpointer = AsyncCheckpointSaver(
                branch=settings.lakebase_branch,
                workspace_client=workspace_client,
                schema="supply_chain_boss",
                check=AsyncConnectionPool.check_connection,
            )
        else:
            raise RuntimeError(
                "Configure a Lakebase checkpoint branch/endpoint or explicitly set "
                "BOSS_CHECKPOINT_BACKEND=memory for local-only runs."
            )
        if isinstance(checkpointer, AsyncCheckpointSaver):
            checkpointer = await resource_stack.enter_async_context(checkpointer)
            await checkpointer.setup()
        specialist = SpecialistClient(settings, workspace_client)
        extractor = PenaltyEvidenceExtractor(settings, workspace_client)
        policy_lookup = VendorPolicyLookup(settings, workspace_client)
        jira_client = JiraMcpClient(settings, workspace_client)
        graph = build_graph(
            settings,
            specialist,
            extractor,
            policy_lookup,
            jira_client,
            checkpointer,
        )
        return BossRuntime(
            graph,
            settings,
            specialist,
            extractor,
            policy_lookup,
            jira_client,
            resource_stack,
        )
    except BaseException:
        await resource_stack.aclose()
        raise


def _thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


async def pending_interrupt(graph: Any, thread_id: str) -> dict[str, Any] | None:
    snapshot = await graph.aget_state(_thread_config(thread_id))
    for task in getattr(snapshot, "tasks", ()):
        for interruption in getattr(task, "interrupts", ()):
            value = getattr(interruption, "value", None)
            if isinstance(value, dict):
                return value
    return None


def thread_id_for(identity: str | None, conversation_id: str) -> str:
    scope = identity or "anonymous"
    return hashlib.sha256(f"{scope}:{conversation_id}".encode()).hexdigest()


def _outcome(state: dict[str, Any], thread_id: str) -> dict[str, Any]:
    interruption = state.get("__interrupt__", ())
    prompt = None
    if interruption:
        first = interruption[0]
        value = getattr(first, "value", None)
        if isinstance(value, dict):
            prompt = value.get("prompt")
    if prompt:
        response = prompt
        status = (
            "approval_pending"
            if state.get("status") == "approval_pending"
            else "awaiting_user_confirmation"
        )
    else:
        response = state.get("response", "The request completed without a response.")
        status = state.get("status", "completed")
    pending = state.get("pending_approval") or {}
    return {
        "conversation_id": thread_id,
        "status": status,
        "response": response,
        "proposal": state.get("proposal"),
        "jira_items": state.get("jira_items", []),
        "approval_issue_url": pending.get("issue_url"),
        "approval_url": (
            pending.get("approval_url")
            or f"{(state.get('app_base_url') or '').rstrip('/')}/approval/{thread_id}"
            if status == "approval_pending" and state.get("app_base_url")
            else None
        ),
    }


def _parse_confirmation(message: str) -> bool | None:
    normalized = message.strip().casefold().strip(" .!?")
    if normalized in {"yes", "y", "confirm", "i confirm", "approve", "approved"}:
        return True
    if normalized in {"no", "n", "cancel", "reject", "do not proceed"}:
        return False
    return None


async def handle_message(
    runtime: BossRuntime,
    conversation_id: str,
    message: str,
    user_id: str | None,
    app_base_url: str | None,
    user_access_token: str | None = None,
) -> dict[str, Any]:
    thread_id = thread_id_for(user_id, conversation_id)
    config = _thread_config(thread_id)
    current_interrupt = await pending_interrupt(runtime.graph, thread_id)
    if current_interrupt:
        if current_interrupt.get("kind") == "manager_approval":
            snapshot = await runtime.graph.aget_state(config)
            values = snapshot.values or {}
            pending = values.get("pending_approval") or {}
            return {
                "conversation_id": thread_id,
                "status": "approval_pending",
                "response": (
                    "This request is waiting for a manager decision. "
                    "Use the Boss App approval page linked from the Jira approval item."
                ),
                "approval_url": pending.get("approval_url"),
                "approval_issue_url": pending.get("issue_url"),
                "jira_items": values.get("jira_items", []),
            }
        if current_interrupt.get("kind") != "user_confirmation":
            raise RuntimeError("Conversation is waiting at an unknown graph interrupt.")
        confirmed = _parse_confirmation(message)
        if confirmed is None:
            return {
                "conversation_id": thread_id,
                "status": "awaiting_user_confirmation",
                "response": "Please reply exactly 'yes' to confirm or 'no' to cancel.",
                "proposal": current_interrupt.get("proposal"),
            }
        with use_user_access_token(user_access_token):
            state = await runtime.graph.ainvoke(
                Command(resume={"approved": confirmed}), config=config
            )
        return _outcome(state, thread_id)

    # A finished turn is reset before reuse of its persistent conversation thread.
    initial_state: BossState = {
        "question": message,
        "app_base_url": app_base_url,
        "thread_id": thread_id,
        "specialist": {},
        "evidence": {},
        "vendor_policy": {},
        "proposal": {},
        "status": "processing",
        "response": "",
        "pending_approval": {},
        "approval_decision": False,
        "jira_items": [],
    }
    with use_user_access_token(user_access_token):
        state = await runtime.graph.ainvoke(initial_state, config=config)
    return _outcome(state, thread_id)


async def manager_approval_details(
    runtime: BossRuntime, thread_id: str
) -> dict[str, Any]:
    config = _thread_config(thread_id)
    current_interrupt = await pending_interrupt(runtime.graph, thread_id)
    snapshot = await runtime.graph.aget_state(config)
    state = snapshot.values or {}
    pending = state.get("pending_approval") or {}
    if (
        not current_interrupt
        or current_interrupt.get("kind") != "manager_approval"
        or current_interrupt.get("request_id") != thread_id
        or pending.get("request_id") != thread_id
        or pending.get("status") != "pending"
    ):
        raise LookupError("No matching pending approval request was found.")
    return {
        "status": "approval_pending",
        "issue_key": pending.get("issue_key"),
        "issue_url": pending.get("issue_url"),
        "proposal": state.get("proposal"),
    }


async def decide_manager_approval(
    runtime: BossRuntime,
    thread_id: str,
    approved: bool,
    user_access_token: str | None = None,
) -> dict[str, Any]:
    config = _thread_config(thread_id)
    current_interrupt = await pending_interrupt(runtime.graph, thread_id)
    snapshot = await runtime.graph.aget_state(config)
    pending = (snapshot.values or {}).get("pending_approval", {})
    if (
        not current_interrupt
        or current_interrupt.get("kind") != "manager_approval"
        or current_interrupt.get("request_id") != thread_id
        or pending.get("request_id") != thread_id
        or pending.get("status") != "pending"
    ):
        raise LookupError("No matching pending approval request was found.")
    with use_user_access_token(user_access_token):
        state = await runtime.graph.ainvoke(
            Command(resume={"request_id": thread_id, "approved": approved}),
            config=config,
        )
    return _outcome(state, thread_id)
