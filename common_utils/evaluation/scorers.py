"""Small deterministic scorers shared by evaluation Jobs and future monitoring."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mlflow.genai.scorers import scorer


def _items(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    return {str(value)}


def _answer(outputs: Any) -> str:
    if isinstance(outputs, Mapping):
        return str(outputs.get("answer") or outputs.get("response") or "")
    return str(outputs or "")


@scorer(name="required_fact_coverage", aggregations=["mean"])
def required_fact_coverage(outputs: Any, expectations: Any) -> float:
    """Return the fraction of expected facts present in the final answer."""
    expected = _items((expectations or {}).get("required_facts") if isinstance(expectations, Mapping) else None)
    if not expected:
        return 1.0
    answer = _answer(outputs).casefold()
    return sum(fact.casefold() in answer for fact in expected) / len(expected)


@scorer(name="tool_routing_accuracy", aggregations=["mean"])
def tool_routing_accuracy(outputs: Any, expectations: Any) -> float:
    """Return the fraction of required managed tool families that were used."""
    expected = _items(
        (expectations or {}).get("expected_tool_families")
        if isinstance(expectations, Mapping)
        else None
    )
    if isinstance(outputs, Mapping):
        evaluation = outputs.get("evaluation") or {}
        observed = _items(
            evaluation.get("tool_families")
            if isinstance(evaluation, Mapping)
            else outputs.get("tool_families")
        )
    else:
        observed = set()
    if not expected:
        return 1.0 if not observed else 0.0
    return len(expected & observed) / len(expected)


CUSTOM_SCORERS = [required_fact_coverage, tool_routing_accuracy]