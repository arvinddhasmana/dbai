"""MLflow logging for evaluation reports, separate from production tracing."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from evaluation.runner import EvaluationReport


_NUMERIC_METRICS = {
    "retrieval_precision",
    "retrieval_recall",
    "retrieval_f1",
    "first_relevant_rank",
    "required_fact_coverage",
    "irrelevant_context_rate",
}


def _judge_model_uri() -> str:
    """Return the configured Databricks-hosted judge endpoint URI."""
    model = os.getenv("JUDGE_MODEL", "databricks-meta-llama-3-1-8b-instruct")
    return model if model.startswith("databricks:/") else f"databricks:/{model}"


def _numeric_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for key in _NUMERIC_METRICS:
        value = metrics.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values[key] = float(value)
    return values


def log_evaluation_report(
    report: EvaluationReport,
    report_path: str | Path | None = None,
    aggregate_metrics: dict[str, float] | None = None,
    genai_rows: list[dict[str, Any]] | None = None,
    mlflow_module: Any | None = None,
) -> str | None:
    """Log an evaluation report and return its MLflow run ID.

    Only aggregate/per-case metrics and non-sensitive run metadata are logged.
    The structured report is stored as an artifact when ``report_path`` is given.
    """
    if mlflow_module is None:
        import mlflow as mlflow_module

    experiment_id = os.getenv("MLFLOW_EXPERIMENT_ID")
    experiment_name = os.getenv("MLFLOW_EXPERIMENT_NAME")
    if experiment_id and hasattr(mlflow_module, "set_experiment"):
        mlflow_module.set_experiment(experiment_id=experiment_id)
    elif experiment_name and hasattr(mlflow_module, "set_experiment"):
        mlflow_module.set_experiment(experiment_name=experiment_name)
    with mlflow_module.start_run(run_name=f"agent-supply-chain-contract-ka-eval-{report.mode}") as run:
        mlflow_module.set_tags({
            "evaluation.dataset": report.dataset,
            "evaluation.mode": report.mode,
            "evaluation.created_at": report.created_at,
        })
        mlflow_module.log_params({
            "evaluation_total_cases": report.total_cases,
            "evaluation_passed_cases": report.passed_cases,
        })
        summary_metrics = {"evaluation_pass_rate": report.pass_rate}
        if aggregate_metrics:
            summary_metrics.update(aggregate_metrics)
        mlflow_module.log_metrics(summary_metrics)
        if report_path:
            mlflow_module.log_artifact(str(report_path), artifact_path="evaluation")
        else:
            mlflow_module.log_dict(report.as_dict(), "evaluation/evaluation_report.json")
        if genai_rows is not None:
            log_genai_evaluation(genai_rows, mlflow_module=mlflow_module)
        return getattr(run, "info", run).run_id


def report_json(report: EvaluationReport) -> str:
    """Serialize a report for callers that need an in-memory payload."""
    return json.dumps(report.as_dict(), indent=2) + "\n"


def log_genai_evaluation(
    rows: list[dict[str, Any]],
    mlflow_module: Any | None = None,
) -> Any:
    """Publish evaluation rows through MLflow GenAI Evaluation.

    Unlike ordinary MLflow metrics, this creates the Evaluation and Score
    records shown by the Databricks Experiment evaluation UI.
    """
    if mlflow_module is None:
        import mlflow as mlflow_module

    from mlflow.genai.scorers import Correctness, RelevanceToQuery, scorer

    experiment_id = os.getenv("MLFLOW_EXPERIMENT_ID")
    experiment_name = os.getenv("MLFLOW_EXPERIMENT_NAME")
    if experiment_id and hasattr(mlflow_module, "set_experiment"):
        mlflow_module.set_experiment(experiment_id=experiment_id)
    elif experiment_name and hasattr(mlflow_module, "set_experiment"):
        mlflow_module.set_experiment(experiment_name=experiment_name)

    def _rows(outputs: dict[str, Any]) -> list[dict[str, Any]]:
        evidence = outputs.get("contract_evidence") or outputs.get("evidence") or {}
        return evidence.get("rows", []) if isinstance(evidence, dict) else []

    def _relevant(row: dict[str, Any], expectations: dict[str, Any]) -> bool:
        expected_ids = set(expectations.get("expected_chunk_ids", []))
        expected_indices = set(expectations.get("expected_chunk_indices", []))
        if expected_ids:
            return row.get("chunk_id") in expected_ids
        if expected_indices:
            return (
                row.get("source_file") == expectations.get("expected_source_file")
                and row.get("chunk_index") in expected_indices
            )
        if expectations.get("expected_source_file"):
            return row.get("source_file") == expectations["expected_source_file"]
        if expectations.get("expected_vendor_id"):
            return row.get("vendor_id") == expectations["expected_vendor_id"]
        return False

    @scorer(name="contract_retrieval_precision", aggregations=["mean"])
    def retrieval_precision(
        outputs: dict[str, Any],
        expectations: dict[str, Any],
    ) -> float:
        rows = _rows(outputs)
        if not rows:
            return 1.0 if expectations.get("expect_empty") else 0.0
        return sum(_relevant(row, expectations) for row in rows) / len(rows)

    @scorer(name="contract_retrieval_recall", aggregations=["mean"])
    def retrieval_recall(
        outputs: dict[str, Any],
        expectations: dict[str, Any],
    ) -> float:
        rows = _rows(outputs)
        expected_ids = set(expectations.get("expected_chunk_ids", []))
        expected_indices = set(expectations.get("expected_chunk_indices", []))
        expected_count = len(expected_ids or expected_indices)
        if not expected_count and expectations.get("expected_source_file"):
            expected_count = 1
        if not expected_count:
            return 1.0 if expectations.get("expect_empty") else 0.0
        return min(sum(_relevant(row, expectations) for row in rows) / expected_count, 1.0)

    @scorer(name="contract_answer_fact_coverage", aggregations=["mean"])
    def answer_fact_coverage(
        outputs: dict[str, Any],
        expectations: dict[str, Any],
    ) -> float:
        facts = expectations.get("required_facts") or expectations.get("expected_keywords") or []
        response = str(outputs.get("response") or outputs.get("answer") or "").lower()
        return sum(str(fact).lower() in response for fact in facts) / len(facts) if facts else 1.0

    @scorer(name="contract_citation_correctness", aggregations=["mean"])
    def citation_correctness(
        outputs: dict[str, Any],
        expectations: dict[str, Any],
    ) -> float:
        source_file = expectations.get("expected_source_file")
        if expectations.get("expect_empty") or not source_file:
            return 1.0
        response = str(outputs.get("response") or outputs.get("answer") or "")
        expected_indices = set(expectations.get("expected_chunk_indices", []))
        if expected_indices:
            return float(any(
                f"[{source_file}, chunk {index}]" in response
                for index in expected_indices
            ))
        return float(f"[{source_file}, chunk " in response)

    scorers = [
        retrieval_precision,
        retrieval_recall,
        answer_fact_coverage,
        citation_correctness,
        Correctness(
            name="contract_correctness",
            model=_judge_model_uri(),
            aggregations=["mean"],
        ),
        RelevanceToQuery(
            name="contract_relevance_to_query",
            model=_judge_model_uri(),
            aggregations=["mean"],
        ),
    ]
    if not experiment_id and experiment_name and hasattr(mlflow_module, "get_experiment_by_name"):
        experiment = mlflow_module.get_experiment_by_name(experiment_name)
        experiment_id = getattr(experiment, "experiment_id", None) if experiment else None
    registered_scorers = [
        current.register(name=f"contract_{current.name}", experiment_id=experiment_id)
        for current in scorers
    ] if experiment_id else scorers

    return mlflow_module.genai.evaluate(
        data=rows,
        scorers=registered_scorers,
    )
