"""Load and validate offline judge calibration fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CalibrationCase:
    case_id: str
    question: str
    answer: str
    contexts: tuple[dict[str, Any], ...]
    required_facts: tuple[str, ...]
    expected: dict[str, float]


def load_calibration(path: str | Path) -> list[CalibrationCase]:
    cases: list[CalibrationCase] = []
    for line_number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            expected = raw["expected"]
            cases.append(
                CalibrationCase(
                    case_id=raw["id"],
                    question=raw["question"],
                    answer=raw["answer"],
                    contexts=tuple(raw["contexts"]),
                    required_facts=tuple(raw.get("required_facts", [])),
                    expected={key: float(value) for key, value in expected.items()},
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid calibration case at {path}:{line_number}") from error
    if not cases:
        raise ValueError(f"Calibration dataset is empty: {path}")
    return cases


def validate_judge_result(case: CalibrationCase, result: Any) -> list[str]:
    """Return threshold failures for one parsed JudgeResult."""
    failures: list[str] = []
    allowed_fields = {
        "context_precision",
        "context_recall",
        "groundedness",
        "completeness",
        "citation_correctness",
        "unsupported_claims",
        "confidence",
    }
    for key, threshold in case.expected.items():
        if key.endswith("_min"):
            field = key.removesuffix("_min")
            if field not in allowed_fields:
                raise ValueError(f"Unsupported calibration threshold: {key}")
            if getattr(result, field) < threshold:
                failures.append(f"{field} below minimum {threshold}")
        elif key.endswith("_max"):
            field = key.removesuffix("_max")
            if field not in allowed_fields:
                raise ValueError(f"Unsupported calibration threshold: {key}")
            if getattr(result, field) > threshold:
                failures.append(f"{field} above maximum {threshold}")
        else:
            raise ValueError(f"Unsupported calibration threshold: {key}")
    return failures
