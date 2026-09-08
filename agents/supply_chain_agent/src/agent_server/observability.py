"""MLflow configuration and redacted tracing helpers."""

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
    """Bind MLflow to the configured experiment without blocking app startup."""
    experiment_id = os.getenv("MLFLOW_EXPERIMENT_ID")
    experiment_name = os.getenv(
        "MLFLOW_EXPERIMENT_NAME",
        "/Shared/globalmart-supply-chain-agent-uc-dev",
    )
    trace_location = UnityCatalog(
        catalog_name=os.getenv("MLFLOW_TRACE_CATALOG", os.getenv("DBAI_CATALOG", "globalmart")),
        schema_name=os.getenv("MLFLOW_TRACE_SCHEMA", "supply_chain"),
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
    span_context = mlflow.start_span(
        name=name,
        span_type=span_type,
        attributes=attributes or {},
    )
    try:
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


def start_agent_span(name, attributes=None):
    """Start a span with only explicitly supplied, non-sensitive attributes."""
    return _start_span(name, SpanType.AGENT, attributes)


def start_retriever_span(name, attributes=None):
    """Start a retrieval span without capturing SQL or contract evidence."""
    return _start_span(name, SpanType.RETRIEVER, attributes)