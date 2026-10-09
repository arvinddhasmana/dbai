import asyncio
from types import SimpleNamespace

import mlflow
import pytest
from mlflow.tracing import set_tracing_context_from_http_request_headers
from opentelemetry import trace

from agent_server.specialist import SpecialistClient
from agent_server.start_server import _traceparent_is_sampled


class FakeHttpClient:
    def __init__(self):
        self.request = None

    async def post(self, url, headers, json):
        self.request = {"url": url, "headers": headers, "json": json}
        return SimpleNamespace(
            is_error=False,
            json=lambda: {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Evidence"}],
                    }
                ],
                "custom_outputs": {},
            },
        )


def test_specialist_request_propagates_active_mlflow_trace(monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACE_PROPAGATE_TO_OTEL_CONTEXT", "true")
    fake_http_client = FakeHttpClient()
    workspace_client = SimpleNamespace(
        apps=SimpleNamespace(
            get=lambda app_name: SimpleNamespace(url="https://specialist.example")
        ),
        config=SimpleNamespace(authenticate=lambda: {"Authorization": "Bearer app"}),
    )
    client = SpecialistClient(
        SimpleNamespace(specialist_app_name="specialist"),
        workspace_client,
        http_client=fake_http_client,
    )
    with mlflow.start_span("boss-specialist-test"):
        result = asyncio.run(client.ask("Find contract evidence", "conversation-1"))
    traceparent = fake_http_client.request["headers"]["traceparent"]
    with set_tracing_context_from_http_request_headers({"traceparent": traceparent}):
        with mlflow.start_span("specialist-child-test"):
            specialist_trace_id = trace.get_current_span().get_span_context().trace_id

    assert result.answer == "Evidence"
    assert fake_http_client.request["url"] == (
        "https://specialist.example/api/responses"
    )
    assert fake_http_client.request["headers"]["Authorization"] == "Bearer app"
    assert f"{specialist_trace_id:032x}" == traceparent.split("-")[1]
    assert fake_http_client.request["json"]["context"]["conversation_id"] == "conversation-1"


@pytest.mark.parametrize(
    ("traceparent", "expected"),
    [
        ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", True),
        ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00", False),
        ("malformed", True),
    ],
)
def test_traceparent_sampling_decision(traceparent, expected):
    assert _traceparent_is_sampled(traceparent) is expected
