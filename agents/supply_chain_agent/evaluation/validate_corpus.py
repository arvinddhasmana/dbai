"""Validate evaluation annotations against a corpus snapshot or live search rows."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from evaluation.runner import EvaluationCase, load_dataset


@dataclass(frozen=True)
class ValidationIssue:
    case_id: str
    message: str


def validate_cases(cases: Iterable[EvaluationCase]) -> list[ValidationIssue]:
    """Validate dataset structure without contacting Databricks."""
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            issues.append(ValidationIssue(case.case_id, "duplicate case id"))
        seen.add(case.case_id)
        if not case.question.strip():
            issues.append(ValidationIssue(case.case_id, "question must not be empty"))
        if case.expect_empty and (
            case.expected_chunk_ids
            or case.expected_chunk_indices
            or case.required_facts
        ):
            issues.append(
                ValidationIssue(
                    case.case_id,
                    "empty-result cases cannot require chunks or facts",
                )
            )
        if not case.expect_empty and not (
            case.expected_vendor_id or case.expected_source_file or case.expected_chunk_ids
        ):
            issues.append(
                ValidationIssue(
                    case.case_id,
                    "non-empty cases need a vendor, source, or chunk expectation",
                )
            )
    return issues


def validate_live_rows(
    case: EvaluationCase,
    rows: list[dict[str, Any]],
) -> list[ValidationIssue]:
    """Validate one case's annotations against ranked corpus rows."""
    issues: list[ValidationIssue] = []
    if case.expect_empty:
        if rows:
            issues.append(ValidationIssue(case.case_id, "expected no rows but rows were returned"))
        return issues

    if case.expected_chunk_ids:
        actual_ids = {row.get("chunk_id") for row in rows}
        missing = sorted(set(case.expected_chunk_ids) - actual_ids)
        if missing:
            issues.append(ValidationIssue(case.case_id, f"missing chunk ids: {missing}"))

    if case.expected_chunk_indices:
        actual_indices = {
            row.get("chunk_index")
            for row in rows
            if row.get("source_file") == case.expected_source_file
        }
        missing = sorted(set(case.expected_chunk_indices) - actual_indices)
        if missing:
            issues.append(ValidationIssue(case.case_id, f"missing chunk indices: {missing}"))

    if case.expected_source_file and not any(
        row.get("source_file") == case.expected_source_file for row in rows
    ):
        issues.append(
            ValidationIssue(
                case.case_id,
                f"expected source file not present: {case.expected_source_file}",
            )
        )

    evidence = " ".join(str(row.get("chunk_text", "")).lower() for row in rows)
    missing_facts = [fact for fact in case.required_facts if fact.lower() not in evidence]
    if missing_facts:
        issues.append(ValidationIssue(case.case_id, f"missing required facts: {missing_facts}"))
    return issues


def load_snapshot(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Load a JSON snapshot keyed by evaluation case ID."""
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or any(not isinstance(rows, list) for rows in raw.values()):
        raise ValueError("Corpus snapshot must be a JSON object mapping case IDs to row lists")
    return raw


def validate_dataset(
    dataset_path: str | Path,
    snapshot_path: str | Path | None = None,
) -> list[ValidationIssue]:
    cases = load_dataset(dataset_path)
    issues = validate_cases(cases)
    if snapshot_path:
        snapshot = load_snapshot(snapshot_path)
        for case in cases:
            if case.case_id not in snapshot:
                issues.append(ValidationIssue(case.case_id, "missing from corpus snapshot"))
            else:
                issues.extend(validate_live_rows(case, snapshot[case.case_id]))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    issues = validate_dataset(args.dataset, args.snapshot)
    if issues:
        for issue in issues:
            print(f"FAIL {issue.case_id}: {issue.message}")
        return 1
    print("PASS evaluation annotations are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())