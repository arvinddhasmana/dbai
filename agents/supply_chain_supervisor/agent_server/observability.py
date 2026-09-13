"""MLflow configuration and redacted supervisor tracing helpers."""

import logging
import os
import sys
from contextlib import contextmanager

import mlflow
from mlflow.entities import SpanType
from mlflow.entities.trace_location import UnityCatalog


logger = logging.getLogger(__name__)


class _NullSpan:
    def set_outputs(self, outputs):
        del outputs


def configure_mlflow():
    """Bind MLflow to the shared contract-agent experiment and UC trace tables."""
    experiment_id = os.getenv("MLFLOW_EXPERIMENT_ID")
    experiment_name = os.getenv(
        "MLFLOW_EXPERIMENT_NAME",
        "/Shared/globalmart-supply-chain-agent-uc-v2-dev",
    )
    trace_location = UnityCatalog(
        catalog_name=os.getenv("MLFLOW_TRACE_CATALOG", os.getenv("DBAI_CATALOG", "globalmart")),
        schema_name=os.getenv("MLFLOW_TRACE_SCHEMA", "agent_observability"),
        table_prefix=os.getenv("MLFLOW_TRACE_TABLE_PREFIX", "contract_agent_traces"),
    )
    try:
        if experiment_id:
            return mlflow.set_experiment(
                experiment_id=experiment_id,
                trace_location=trace_location,
            )
        return mlflow.set_experiment(
            experiment_name=experiment_name,
            trace_location=trace_location,
        )
    except Exception:
        logger.warning(
            "MLflow experiment binding failed; request handling will continue.",
            exc_info=True,
        )
        return None


@contextmanager
def _start_span(name, span_type, attributes):
    try:
        span_context = mlflow.start_span(
            name=name,
            span_type=span_type,
            attributes=attributes or {},
        )
        span = span_context.__enter__()
    except Exception:
        logger.warning("MLflow span failed to start; continuing without this span.", exc_info=True)
        yield _NullSpan()
        return

    try:
        yield span
    finally:
        try:
            span_context.__exit__(*sys.exc_info())
        except Exception:
            logger.warning("MLflow span failed to close; continuing without this span.", exc_info=True)


def start_mcp_span(server_name, attributes=None):
    """Trace MCP discovery without recording endpoint URLs or error payloads."""
    span_attributes = {"server_name": server_name}
    span_attributes.update(attributes or {})
    return _start_span("supervisor.mcp", SpanType.TOOL, span_attributes)