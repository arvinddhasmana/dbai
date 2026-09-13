import asyncio
from contextlib import AsyncExitStack, contextmanager

import os

import pytest
from mlflow.types.responses import ResponsesAgentRequest

from agent_server import agent
from agent_server import observability


def test_vector_search_path_requires_three_part_index(monkeypatch):
    monkeypatch.setattr(agent, "VECTOR_INDEX", "globalmart.supply_chain.index")
    assert agent._vector_search_path() == (
        "/api/2.0/mcp/vector-search/globalmart/supply_chain/index"
    )


def test_default_model_endpoint_is_available_chat_endpoint():
    assert agent.MODEL_ENDPOINT == "databricks-meta-llama-3-3-70b-instruct"


def test_mlflow_uses_shared_contract_trace_location(monkeypatch):
    calls = {}

    def set_experiment(**kwargs):
        calls.update(kwargs)
        return "experiment"

    monkeypatch.setenv("MLFLOW_EXPERIMENT_ID", "4341372968956549")
    monkeypatch.setenv("MLFLOW_TRACE_CATALOG", "globalmart")
    monkeypatch.setenv("MLFLOW_TRACE_SCHEMA", "agent_observability")
    monkeypatch.setenv("MLFLOW_TRACE_TABLE_PREFIX", "contract_agent_traces")
    monkeypatch.setattr(observability.mlflow, "set_experiment", set_experiment)

    assert observability.configure_mlflow() == "experiment"
    assert calls["experiment_id"] == "4341372968956549"
    assert calls["trace_location"].catalog_name == "globalmart"
    assert calls["trace_location"].schema_name == "agent_observability"
    assert calls["trace_location"].table_prefix == "contract_agent_traces"


def test_unavailable_vector_search_does_not_block_genie(monkeypatch):
    span_outputs = []

    @contextmanager
    def fake_span(server_name):
        del server_name

        class Span:
            def set_outputs(self, outputs):
                span_outputs.append(outputs)

        yield Span()

    class FailingServer:
        name = "vendor contract managed Vector Search"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def list_tools(self):
            raise RuntimeError("endpoint unavailable")

    class HealthyServer:
        name = "inventory Genie"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def list_tools(self):
            return []

    monkeypatch.setattr(agent, "start_mcp_span", fake_span)

    async def run():
        async with AsyncExitStack() as stack:
            return await agent.connect_healthy_mcp_servers(
                stack,
                [FailingServer(), HealthyServer()],
            )

    healthy, unavailable = asyncio.run(run())

    assert [server.name for server in healthy] == ["inventory Genie"]
    assert unavailable == ["vendor contract managed Vector Search"]
    assert span_outputs == [
        {"status": "unavailable", "error_type": "RuntimeError"},
        {"status": "available"},
    ]


def test_vector_search_path_rejects_unqualified_index(monkeypatch):
    monkeypatch.setattr(agent, "VECTOR_INDEX", "index")
    with pytest.raises(RuntimeError, match="three-part"):
        agent._vector_search_path()


def test_lakebase_options_are_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("LAKEBASE_AUTOSCALING_ENDPOINT", raising=False)
    monkeypatch.delenv("LAKEBASE_AUTOSCALING_PROJECT", raising=False)
    monkeypatch.delenv("LAKEBASE_AUTOSCALING_BRANCH", raising=False)
    assert agent._lakebase_options() is None


def test_metadata_reports_project_branch_lakebase(monkeypatch):
    monkeypatch.delenv("LAKEBASE_AUTOSCALING_ENDPOINT", raising=False)
    monkeypatch.setenv("LAKEBASE_AUTOSCALING_PROJECT", "project")
    monkeypatch.setenv("LAKEBASE_AUTOSCALING_BRANCH", "branch")

    assert agent._metadata("session", "user", [])["history_backend"] == "lakebase"


def test_lakebase_options_accept_full_branch_path(monkeypatch):
    monkeypatch.delenv("LAKEBASE_AUTOSCALING_ENDPOINT", raising=False)
    monkeypatch.delenv("LAKEBASE_AUTOSCALING_PROJECT", raising=False)
    monkeypatch.setenv(
        "LAKEBASE_AUTOSCALING_BRANCH",
        "projects/project/branches/production",
    )
    assert agent._lakebase_options(object())["branch"] == "projects/project/branches/production"


def test_scoped_session_id_is_user_specific():
    first = agent._scoped_session_id("conversation", "user-a")
    second = agent._scoped_session_id("conversation", "user-b")

    assert first != second
    assert first == agent._scoped_session_id("conversation", "user-a")


def test_obo_identity_failure_does_not_use_client_user_id():
    class FailingWorkspaceClient:
        class CurrentUser:
            @staticmethod
            def me():
                raise RuntimeError("identity unavailable")

        current_user = CurrentUser()

    request = ResponsesAgentRequest(
        input=[{"role": "user", "content": "hello"}],
        custom_inputs={"user_id": "spoofed-user"},
    )

    assert agent.get_user_id(request, FailingWorkspaceClient()) is None