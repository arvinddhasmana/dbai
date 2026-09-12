"""Framework-neutral models shared by agent evaluation Jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvaluationCase:
    """One governed test case independent of an agent implementation."""

    case_id: str
    question: str
    scenario: str = "general"
    required_facts: tuple[str, ...] = ()
    expected_tool_families: tuple[str, ...] = ()
    reference_answer: str | None = None
    expected_behavior: str | None = None

    def expectations(self) -> dict[str, Any]:
        values: dict[str, Any] = {
            "case_id": self.case_id,
            "scenario": self.scenario,
            "required_facts": list(self.required_facts),
            "expected_tool_families": list(self.expected_tool_families),
        }
        if self.reference_answer is not None:
            values["reference_answer"] = self.reference_answer
        if self.expected_behavior is not None:
            values["expected_behavior"] = self.expected_behavior
        return values


@dataclass(frozen=True)
class AgentObservation:
    """Sanitized result returned by an evaluator adapter."""

    answer: str
    tool_families: tuple[str, ...] = ()
    tool_calls: tuple[dict[str, str], ...] = ()
    errors: tuple[str, ...] = ()
    sources: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def outputs(self) -> dict[str, Any]:
        """Return an MLflow-compatible output payload without sensitive request data."""
        evaluation = {
            "tool_families": list(self.tool_families),
            "tool_calls": [dict(call) for call in self.tool_calls],
        }
        return {
            "answer": self.answer,
            "response": self.answer,
            "sources": [dict(source) for source in self.sources],
            "errors": list(self.errors),
            "evaluation": evaluation,
            "metadata": dict(self.metadata),
        }