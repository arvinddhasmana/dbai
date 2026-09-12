"""Dataset loading and normalization for Unity Catalog-backed evaluation data."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from common_utils.evaluation.models import EvaluationCase


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parsed = _mapping(value)
        if parsed:
            return tuple(str(item) for item in parsed.values())
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    return (str(value),)


def case_from_record(record: Mapping[str, Any]) -> EvaluationCase:
    """Convert a UC/MLflow dataset record to the shared case model."""
    inputs = _mapping(record.get("inputs"))
    expectations = _mapping(record.get("expectations"))
    case_id = str(
        expectations.get("case_id")
        or inputs.get("case_id")
        or record.get("case_id")
        or ""
    ).strip()
    question = str(inputs.get("question") or record.get("question") or "").strip()
    if not case_id or not question:
        raise ValueError("Evaluation records require non-empty case_id and question values")
    return EvaluationCase(
        case_id=case_id,
        question=question,
        scenario=str(expectations.get("scenario") or record.get("scenario") or "general"),
        required_facts=_items(expectations.get("required_facts") or record.get("required_facts")),
        expected_tool_families=_items(
            expectations.get("expected_tool_families")
            or record.get("expected_tool_families")
        ),
        reference_answer=expectations.get("reference_answer") or record.get("reference_answer"),
        expected_behavior=expectations.get("expected_behavior") or record.get("expected_behavior"),
    )


def load_uc_dataset(name: str, version: int | None = None):
    """Load a managed MLflow EvaluationDataset backed by a UC table."""
    from mlflow.genai.datasets import get_dataset

    return get_dataset(name=name, version=version)


def cases_from_dataset(dataset: Any) -> list[EvaluationCase]:
    """Read the current dataset version into framework cases for adapter lookup."""
    frame = dataset.to_df()
    records = frame.to_dict(orient="records")
    cases = [case_from_record(record) for record in records]
    if not cases:
        raise ValueError(f"Evaluation dataset {dataset.name!r} is empty")
    return cases