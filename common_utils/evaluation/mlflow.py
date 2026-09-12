"""MLflow GenAI evaluation harness shared by deployed agent evaluators."""

from __future__ import annotations

import os
from typing import Any, Callable


DEFAULT_JUDGE_MODEL = "databricks-meta-llama-3-1-8b-instruct"


def judge_model_uri(model: str) -> str:
    return model if model.startswith("databricks:/") else f"databricks:/{model}"


def evaluate_dataset(
    dataset: Any,
    predict_fn: Callable[..., Any],
    experiment_id: str | None = None,
    experiment_name: str | None = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
) -> Any:
    """Run exactly two built-in judges and the shared deterministic scorers."""
    import mlflow
    from mlflow.genai.scorers import Correctness, RelevanceToQuery

    if experiment_id:
        mlflow.set_experiment(experiment_id=experiment_id)
    elif experiment_name:
        mlflow.set_experiment(experiment_name=experiment_name)
    else:
        raise ValueError("An MLflow experiment ID or name is required")

    from common_utils.evaluation.scorers import CUSTOM_SCORERS

    scorers = [
        Correctness(
            name="supervisor_correctness",
            model=judge_model_uri(judge_model),
            aggregations=["mean"],
        ),
        RelevanceToQuery(
            name="supervisor_relevance_to_query",
            model=judge_model_uri(judge_model),
            aggregations=["mean"],
        ),
        *CUSTOM_SCORERS,
    ]
    return mlflow.genai.evaluate(data=dataset, predict_fn=predict_fn, scorers=scorers)