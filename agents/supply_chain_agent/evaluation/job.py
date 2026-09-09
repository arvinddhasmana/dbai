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
    create_databricks_judge_client,
    default_agent_invoker,
    invoke_agent,
    judge_agent_result,
    profile_agent_invoker,
)
from evaluation.mlflow_logging import log_evaluation_report, log_genai_evaluation
from evaluation.runner import EvaluationReport, _mock_search, load_dataset, score_case


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
    judge_results = []
    evaluation_rows = []
    judge_client = create_databricks_judge_client(profile) if mode == "live" else None
    for case in cases:
        if mode == "mock":
            retrieval = _mock_search(case.question, case.expected_vendor_id)
            agent_result = None
            judged = None
        else:
            agent_result = invoke_agent(case, invoker)
            retrieval = agent_result.evidence
            judged = judge_agent_result(
                case,
                agent_result,
                judge_client,
                judge_model,
            )
            judge_results.append((case, agent_result, judged))
        case_result = score_case(case, retrieval)
        results.append(case_result)
        evaluation_rows.append({
            "inputs": {"question": case.question},
            "outputs": {
                "case_id": case.case_id,
                "passed": case_result.passed,
                "metrics": case_result.metrics,
                "answer": agent_result.answer if agent_result else None,
                "evidence": retrieval,
                "judge": judged.as_dict() if judged else {},
            },
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
    report_payload["judge_results"] = {
        case.case_id: judged.as_dict()
        for case, _, judged in judge_results
    }
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
        genai_rows=evaluation_rows if judge_results else None,
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=_parameter("DATASET", "evaluation/baseline.jsonl"))
    parser.add_argument("--mode", choices=("mock", "live"), default=_parameter("MODE", "mock"))
    parser.add_argument("--output", default=_parameter("OUTPUT", "evaluation/results.json"))
    parser.add_argument(
        "--app-url",
        default=_parameter(
            "AGENT_APP_URL",
            "https://dbai-supply-agent-dev-7405617519191024.4.azure.databricksapps.com",
        ),
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
    args = parser.parse_args()
    _configure_mlflow_tracking(args.mlflow_tracking_uri, args.profile)
    if args.mlflow_tracing_sql_warehouse_id:
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = args.mlflow_tracing_sql_warehouse_id
    os.environ["MLFLOW_EXPERIMENT_NAME"] = args.mlflow_experiment_name
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
