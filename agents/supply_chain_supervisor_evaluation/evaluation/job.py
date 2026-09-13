# Databricks notebook source
"""Evaluate the deployed GlobalMart supervisor against a UC-backed dataset."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    candidates = [Path.cwd(), *Path.cwd().parents]
    source_file = globals().get("__file__")
    if source_file:
        candidates.insert(0, Path(source_file).resolve().parents[3])
    for candidate in candidates:
        if (candidate / "common_utils" / "evaluation").is_dir():
            return candidate
    return Path.cwd()


_REPO_ROOT = _repo_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common_utils.evaluation.adapters import (  # noqa: E402
    SupervisorAdapter,
    profile_agent_invoker,
    token_agent_invoker,
)
from common_utils.evaluation.dataset import cases_from_dataset, load_uc_dataset  # noqa: E402
from common_utils.evaluation.mlflow import (  # noqa: E402
    DEFAULT_JUDGE_MODEL,
    evaluate_dataset,
)
from common_utils.evaluation.models import AgentObservation, EvaluationCase  # noqa: E402


def _parameter(name: str, default: str | None = None) -> str | None:
    try:
        dbutils.widgets.text(name, os.getenv(name, default or ""))
        return dbutils.widgets.get(name) or default
    except NameError:
        return os.getenv(name, default)


def _configure_mlflow_tracing_sql_warehouse(warehouse_id: str | None) -> None:
    if warehouse_id:
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = warehouse_id


def _configure_mlflow_trace_propagation(enabled: str | None) -> None:
    if enabled:
        os.environ["MLFLOW_TRACE_PROPAGATE_TO_OTEL_CONTEXT"] = enabled


def _mock_observation(case: EvaluationCase) -> AgentObservation:
    answer = case.reference_answer or " ".join(case.required_facts)
    return AgentObservation(
        answer=answer,
        tool_families=case.expected_tool_families,
        metadata={"mode": "mock"},
    )


def _error_observation(error: Exception) -> AgentObservation:
    detail = " ".join(str(error).split())[:200]
    return AgentObservation(
        answer="",
        errors=(f"{type(error).__name__}: {detail}" if detail else type(error).__name__,),
        metadata={"mode": "live", "error": type(error).__name__},
    )


def run(
    dataset_name: str,
    mode: str,
    app_url: str | None,
    app_name: str | None,
    profile: str | None,
    token: str | None,
    experiment_id: str | None,
    experiment_name: str | None,
    judge_model: str,
    dataset_version: int | None = None,
    output: Path | None = None,
) -> Any:
    if mode not in {"mock", "live"}:
        raise ValueError("MODE must be mock or live")
    if mode == "live" and not app_url:
        raise ValueError("Live evaluation requires SUPERVISOR_APP_URL")
    if not experiment_id and not experiment_name:
        raise ValueError("Evaluation requires MLFLOW_EXPERIMENT_ID or MLFLOW_EXPERIMENT_NAME")

    dataset = load_uc_dataset(dataset_name, version=dataset_version)
    cases = cases_from_dataset(dataset)
    cases_by_id = {case.case_id: case for case in cases}
    adapter = None
    if mode == "live":
        invoker = (
            token_agent_invoker(app_url, token)
            if token
            else profile_agent_invoker(app_url, profile, app_name)
        )
        adapter = SupervisorAdapter(invoker)

    def predict_fn(case_id: str | Mapping[str, Any], question: str | None = None, **_: Any) -> dict[str, Any]:
        if isinstance(case_id, Mapping):
            inputs = case_id
            case_id = str(inputs.get("case_id") or "")
            question = str(inputs.get("question") or "")
        if question is None:
            raise ValueError("Evaluation inputs require a question")
        case = cases_by_id.get(case_id) or EvaluationCase(case_id=case_id, question=question)
        try:
            observation = _mock_observation(case) if mode == "mock" else adapter.invoke(case)
        except Exception as error:
            observation = _error_observation(error)
        return observation.outputs()

    result = evaluate_dataset(
        dataset=dataset,
        predict_fn=predict_fn,
        experiment_id=experiment_id,
        experiment_name=experiment_name,
        judge_model=judge_model,
    )
    summary = {
        "dataset": dataset_name,
        "dataset_version": getattr(dataset, "version", None),
        "mode": mode,
        "experiment_id": experiment_id,
        "experiment_name": experiment_name,
        "judge_model": judge_model,
        "scorers": [
            "supervisor_correctness",
            "supervisor_relevance_to_query",
            "required_fact_coverage",
            "tool_routing_accuracy",
        ],
        "run_id": getattr(result, "run_id", None),
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _configure_mlflow_trace_propagation(
        _parameter("MLFLOW_TRACE_PROPAGATE_TO_OTEL_CONTEXT", "true")
    )
    _configure_mlflow_tracing_sql_warehouse(
        _parameter("MLFLOW_TRACING_SQL_WAREHOUSE_ID")
    )
    dataset_version_value = _parameter("EVALUATION_DATASET_VERSION")
    parser.add_argument("--dataset", default=_parameter("EVALUATION_DATASET", "globalmart.agent_evaluation.supervisor_cases"))
    parser.add_argument("--mode", choices=("mock", "live"), default=_parameter("MODE", "mock"))
    parser.add_argument("--app-url", default=_parameter("SUPERVISOR_APP_URL"))
    parser.add_argument("--app-name", default=_parameter("SUPERVISOR_APP_NAME"))
    parser.add_argument("--profile", default=_parameter("DATABRICKS_PROFILE"))
    parser.add_argument("--token", default=_parameter("DATABRICKS_TOKEN"))
    parser.add_argument(
        "--experiment-id",
        default=_parameter("MLFLOW_EXPERIMENT_ID", "4341372968956549"),
    )
    parser.add_argument(
        "--experiment-name",
        default=_parameter("MLFLOW_EXPERIMENT_NAME", "/Shared/globalmart-supply-chain-agent-uc-v2-dev"),
    )
    parser.add_argument("--judge-model", default=_parameter("JUDGE_MODEL", DEFAULT_JUDGE_MODEL))
    parser.add_argument(
        "--dataset-version",
        type=int,
        default=int(dataset_version_value) if dataset_version_value else None,
    )
    parser.add_argument("--output", type=Path, default=Path(_parameter("OUTPUT", "evaluation/results.json")))
    args = parser.parse_args([] if "dbutils" in globals() else None)
    run(
        dataset_name=args.dataset,
        mode=args.mode,
        app_url=args.app_url,
        app_name=args.app_name,
        profile=args.profile,
        token=args.token,
        experiment_id=args.experiment_id,
        experiment_name=args.experiment_name,
        judge_model=args.judge_model,
        dataset_version=args.dataset_version,
        output=args.output,
    )


if __name__ == "__main__":
    main()