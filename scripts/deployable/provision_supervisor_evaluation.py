"""Provision the supervisor MLflow experiment and Unity Catalog evaluation dataset."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors.platform import NotFound


DEFAULT_EXPERIMENT = "/Shared/globalmart-supply-chain-supervisor-evaluation-dev"
DEFAULT_DATASET = "globalmart.agent_evaluation.supervisor_cases"
DEFAULT_SEED = Path(__file__).parents[2] / "agents/supply_chain_supervisor_evaluation/evaluation/dataset.jsonl"


def _state(response: Any) -> str:
    value = response.status.state
    return getattr(value, "value", str(value).rsplit(".", 1)[-1])


def execute_sql(client: WorkspaceClient, statement: str, warehouse_id: str) -> None:
    response = client.statement_execution.execute_statement(
        statement,
        warehouse_id=warehouse_id,
        wait_timeout="30s",
    )
    while _state(response) in {"PENDING", "RUNNING"}:
        time.sleep(2)
        response = client.statement_execution.get_statement(response.statement_id)
    if _state(response) != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or str(error) or "unknown SQL error"
        raise RuntimeError(f"Databricks SQL failed: {message}")


def ensure_schema(client: WorkspaceClient, catalog: str, schema: str, warehouse_id: str) -> None:
    execute_sql(
        client,
        f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`",
        warehouse_id,
    )


def resolve_experiment(name: str) -> str:
    import mlflow

    experiment = mlflow.get_experiment_by_name(name)
    if experiment is None:
        experiment_id = mlflow.create_experiment(name)
    else:
        experiment_id = experiment.experiment_id
    return str(experiment_id)


def load_seed_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid seed record at {path}:{line_number}") from error
        if not isinstance(record, dict):
            raise ValueError(f"Seed record at {path}:{line_number} must be an object")
        records.append(record)
    if not records:
        raise ValueError(f"Seed dataset is empty: {path}")
    return records


def _record_case_id(record: dict[str, Any]) -> str:
    expectations = record.get("expectations") or {}
    inputs = record.get("inputs") or {}
    return str(expectations.get("case_id") or inputs.get("case_id") or "")


def _record_expectations(record: dict[str, Any]) -> dict[str, Any]:
    expectations = record.get("expectations")
    return expectations if isinstance(expectations, dict) else {}


def provision(
    profile: str | None,
    catalog: str,
    schema: str,
    warehouse_id: str,
    experiment_name: str,
    dataset_name: str,
    seed_path: Path,
) -> dict[str, Any]:
    if not warehouse_id:
        raise ValueError("A SQL warehouse ID is required to provision the UC schema")

    os.environ["MLFLOW_TRACKING_URI"] = f"databricks://{profile}" if profile else "databricks"
    os.environ["MLFLOW_REGISTRY_URI"] = "databricks-uc"
    import mlflow
    from mlflow.exceptions import MlflowException
    from mlflow.genai.datasets import create_dataset, get_dataset

    client = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    ensure_schema(client, catalog, schema, warehouse_id)
    experiment_id = resolve_experiment(experiment_name)
    mlflow.set_experiment(experiment_id=experiment_id)

    try:
        dataset = get_dataset(name=dataset_name)
        created = False
    except (MlflowException, NotFound) as error:
        message = str(error).lower()
        if "not found" not in message and "does not exist" not in message:
            raise
        dataset = create_dataset(name=dataset_name, experiment_id=experiment_id)
        created = True

    seed_records = load_seed_records(seed_path)
    existing_records: dict[str, dict[str, Any]] = {}
    if not created and dataset.has_records():
        for row in dataset.to_df().to_dict(orient="records"):
            existing_records[_record_case_id(row)] = row
    records_to_merge = []
    for record in seed_records:
        current = existing_records.get(_record_case_id(record))
        if current is None or _record_expectations(current) != _record_expectations(record):
            records_to_merge.append(record)
    if records_to_merge:
        dataset.merge_records(records_to_merge)
        dataset = get_dataset(name=dataset_name)

    result = {
        "experiment_name": experiment_name,
        "experiment_id": experiment_id,
        "dataset_name": dataset.name,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "seed_records": len(seed_records),
        "records_updated_or_added": len(records_to_merge),
        "schema": f"{catalog}.{schema}",
    }
    print(json.dumps(result, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.getenv("DATABRICKS_CONFIG_PROFILE"))
    parser.add_argument("--catalog", default=os.getenv("DBAI_CATALOG", "globalmart"))
    parser.add_argument("--schema", default="agent_evaluation")
    parser.add_argument("--warehouse-id", default=os.getenv("DATABRICKS_SQL_WAREHOUSE_ID", "a749a7ee30b8f4f4"))
    parser.add_argument("--experiment-name", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    args = parser.parse_args()
    provision(
        profile=args.profile,
        catalog=args.catalog,
        schema=args.schema,
        warehouse_id=args.warehouse_id,
        experiment_name=args.experiment_name,
        dataset_name=args.dataset,
        seed_path=args.seed,
    )


if __name__ == "__main__":
    main()