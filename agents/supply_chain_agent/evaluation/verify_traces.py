"""Verify that deployed agent requests produce the expected MLflow spans."""

from __future__ import annotations

import argparse
import os

import mlflow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        default=os.getenv(
            "MLFLOW_EXPERIMENT_NAME",
            "/Shared/globalmart-supply-chain-agent-uc-dev",
        ),
    )
    parser.add_argument(
        "--trace-location",
        default=os.getenv(
            "MLFLOW_TRACE_LOCATION",
            ".".join(
                (
                    os.getenv("MLFLOW_TRACE_CATALOG", os.getenv("DBAI_CATALOG", "globalmart")),
                    os.getenv("MLFLOW_TRACE_SCHEMA", "supply_chain"),
                    os.getenv("MLFLOW_TRACE_TABLE_PREFIX", "contract_agent_traces"),
                )
            ),
        ),
        help="Unity Catalog trace location: catalog.schema[.table_prefix].",
    )
    parser.add_argument("--max-results", type=int, default=20)
    args = parser.parse_args()

    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "databricks"))
    experiment = mlflow.get_experiment_by_name(args.experiment)
    if experiment is None:
        raise SystemExit(f"MLflow experiment not found: {args.experiment}")

    traces = mlflow.search_traces(
        locations=[args.trace_location],
        max_results=args.max_results,
        return_type="list",
        flush=True,
    )
    if not traces:
        raise SystemExit("No MLflow traces found. Invoke the deployed App first.")

    span_names = {
        span.name
        for trace in traces
        for span in trace.data.spans
    }
    required = {"contract_agent.invoke", "contract_search"}
    missing = required - span_names
    if missing:
        raise SystemExit(f"Missing expected span names: {', '.join(sorted(missing))}")

    print({
        "experiment": args.experiment,
        "trace_count": len(traces),
        "span_names": sorted(span_names),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())