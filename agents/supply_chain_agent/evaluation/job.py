# Databricks notebook source
"""Repeatable Databricks evaluation entry point for the contract agent."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from evaluation.adapters import (
    DEFAULT_JUDGE_MODEL,
    default_agent_invoker,
    invoke_agent,
    profile_agent_invoker,
)
from evaluation.mlflow_logging import log_evaluation_report, log_genai_evaluation
from evaluation.runner import (
    EvaluationReport,
    _mock_search,
    load_dataset,
    score_case,
)


def _parameter(name: str, default: str | None = None) -> str | None:
    try:
        dbutils.widgets.text(name, os.getenv(name, default or ""))
        return dbutils.widgets.get(name) or default
    except NameError:
        return os.getenv(name, default)


def _configure_mlflow_tracking(tracking_uri: str | None, profile: str | None) -> str | None:
    """Select Databricks-hosted tracking for Jobs and profile-authenticated local runs."""
    selected = tracking_uri or (f"databricks://{profile}" if profile else None)
    if selected:
        os.environ["MLFLOW_TRACKING_URI"] = selected
    if profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
    return selected


def run(
    dataset: Path,
    mode: str,
    output: Path,
    app_url: str | None,
    token: str | None,
    profile: str | None,
    judge_model: str,
) -> EvaluationReport:
    cases = load_dataset(dataset)
    if mode == "live" and app_url and token:
        invoker = default_agent_invoker(app_url, token)
    elif mode == "live" and app_url:
        invoker = profile_agent_invoker(app_url, profile)
    else:
        invoker = None
    if mode == "live" and invoker is None:
        raise ValueError("Live evaluation requires AGENT_APP_URL and DATABRICKS_TOKEN")

    results = []
    evaluation_rows = []
    for case in cases:
        if mode == "mock":
            retrieval = _mock_search(case.question, case.expected_vendor_id)
            agent_result = None
        else:
            agent_result = invoke_agent(case, invoker)
            retrieval = agent_result.evidence
        case_result = score_case(case, retrieval)
        results.append(case_result)
        expected_response = case.reference_answer
        if expected_response is None and case.expect_empty:
            expected_response = "No active contract evidence was found."
        expectations = {
            key: value
            for key, value in {
                "expected_vendor_id": case.expected_vendor_id,
                "expected_source_file": case.expected_source_file,
                "expected_chunk_indices": list(case.expected_chunk_indices),
                "expected_chunk_ids": list(case.expected_chunk_ids),
                "expected_keywords": list(case.expected_keywords),
                "required_facts": list(case.required_facts),
                "reference_answer": expected_response,
                "expect_empty": case.expect_empty,
                "expected_error_code": case.expected_error_code,
                "expected_facts": list(case.required_facts or case.expected_keywords),
                "expected_response": expected_response,
            }.items()
            if value is not None
        }
        evaluation_rows.append({
            "inputs": {"question": case.question},
            "outputs": {
                "case_id": case.case_id,
                "passed": case_result.passed,
                "metrics": case_result.metrics,
                "response": agent_result.answer if agent_result else None,
                "answer": agent_result.answer if agent_result else None,
                "contract_evidence": retrieval,
                "evidence": retrieval,
            },
            "expectations": expectations,
        })

    report = EvaluationReport(
        dataset=str(dataset),
        mode=mode,
        created_at=datetime.now(timezone.utc).isoformat(),
        total_cases=len(results),
        passed_cases=sum(result.passed for result in results),
        results=tuple(results),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    report_payload = report.as_dict()
    output.write_text(json.dumps(report_payload, indent=2) + "\n")
    metric_names = {
        "retrieval_precision": "retrieval_precision_mean",
        "retrieval_recall": "retrieval_recall_mean",
        "retrieval_f1": "retrieval_f1_mean",
        "required_fact_coverage": "required_fact_coverage_mean",
        "irrelevant_context_rate": "irrelevant_context_rate_mean",
    }
    aggregate_metrics = {
        metric_names[metric]: mean(
            float(result.metrics[metric])
            for result in results
            if isinstance(result.metrics.get(metric), (int, float))
            and not isinstance(result.metrics.get(metric), bool)
        )
        for metric in (
            "retrieval_precision",
            "retrieval_recall",
            "retrieval_f1",
            "required_fact_coverage",
            "irrelevant_context_rate",
        )
        if any(metric in result.metrics for result in results)
    }
    log_evaluation_report(
        report,
        report_path=output,
        aggregate_metrics=aggregate_metrics,
        genai_rows=evaluation_rows if mode == "live" else None,
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=_parameter("DATASET", "evaluation/baseline.jsonl"))
    parser.add_argument("--mode", choices=("mock", "live"), default=_parameter("MODE", "mock"))
    parser.add_argument("--output", default=_parameter("OUTPUT", "evaluation/results.json"))
    parser.add_argument(
        "--app-url",
        default=_parameter("AGENT_APP_URL"),
    )
    parser.add_argument("--token", default=_parameter("DATABRICKS_TOKEN"))
    parser.add_argument("--profile", default=_parameter("DATABRICKS_PROFILE"))
    parser.add_argument("--mlflow-tracking-uri", default=_parameter("MLFLOW_TRACKING_URI"))
    parser.add_argument(
        "--mlflow-tracing-sql-warehouse-id",
        default=_parameter("MLFLOW_TRACING_SQL_WAREHOUSE_ID"),
    )
    parser.add_argument("--judge-model", default=_parameter("JUDGE_MODEL", DEFAULT_JUDGE_MODEL))
    parser.add_argument(
        "--mlflow-experiment-name",
        default=_parameter(
            "MLFLOW_EXPERIMENT_NAME",
            "/Shared/globalmart-supply-chain-agent-uc-v2-dev",
        ),
    )
    parser.add_argument(
        "--mlflow-experiment-id",
        default=_parameter("MLFLOW_EXPERIMENT_ID", "4341372968956549"),
    )
    args = parser.parse_args()
    _configure_mlflow_tracking(args.mlflow_tracking_uri, args.profile)
    if args.mlflow_tracing_sql_warehouse_id:
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = args.mlflow_tracing_sql_warehouse_id
    os.environ["MLFLOW_EXPERIMENT_NAME"] = args.mlflow_experiment_name
    if args.mlflow_experiment_id:
        os.environ["MLFLOW_EXPERIMENT_ID"] = args.mlflow_experiment_id
    os.environ["JUDGE_MODEL"] = args.judge_model
    report = run(
        Path(args.dataset),
        args.mode,
        Path(args.output),
        args.app_url,
        args.token,
        args.profile,
        args.judge_model,
    )
    print(f"Evaluation complete: {report.passed_cases}/{report.total_cases} passed")


if __name__ == "__main__":
    main()
