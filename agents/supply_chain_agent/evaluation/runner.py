"""Evaluate contract retrieval evidence without scoring generated prose."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    expected_vendor_id: str | None = None
    expected_source_file: str | None = None
    expected_chunk_indices: tuple[int, ...] = ()
    expected_keywords: tuple[str, ...] = ()
    expect_empty: bool = False
    expected_error_code: str | None = None


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    metrics: dict[str, Any]
    failure: str | None = None


@dataclass(frozen=True)
class EvaluationReport:
    dataset: str
    mode: str
    created_at: str
    total_cases: int
    passed_cases: int
    results: tuple[CaseResult, ...]

    @property
    def pass_rate(self) -> float:
        return self.passed_cases / self.total_cases if self.total_cases else 0.0

    def as_dict(self):
        payload = asdict(self)
        payload["results"] = [asdict(result) for result in self.results]
        payload["pass_rate"] = self.pass_rate
        return payload


def load_dataset(path: str | Path) -> list[EvaluationCase]:
    cases = []
    for line_number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            cases.append(
                EvaluationCase(
                    case_id=raw["id"],
                    question=raw["question"],
                    expected_vendor_id=raw.get("expected_vendor_id"),
                    expected_source_file=raw.get("expected_source_file"),
                    expected_chunk_indices=tuple(raw.get("expected_chunk_indices", [])),
                    expected_keywords=tuple(raw.get("expected_keywords", [])),
                    expect_empty=raw.get("expect_empty", False),
                    expected_error_code=raw.get("expected_error_code"),
                )
            )
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid evaluation case at {path}:{line_number}") from error
    if not cases:
        raise ValueError(f"Evaluation dataset is empty: {path}")
    return cases


def score_case(case: EvaluationCase, raw_result: str | dict[str, Any]) -> CaseResult:
    result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    rows = result.get("rows", []) if result.get("ok") else []
    metrics: dict[str, Any] = {
        "ok_match": (result.get("ok") is not None) and (
            result.get("ok") is (case.expected_error_code is None)
        ),
        "row_count": len(rows),
    }

    if case.expected_error_code:
        metrics["error_code_match"] = result.get("error_code") == case.expected_error_code
        passed = metrics["ok_match"] and metrics["error_code_match"]
        failure = None if passed else "Expected the configured search error contract."
        return CaseResult(case.case_id, passed, metrics, failure)

    metrics["empty_match"] = (len(rows) == 0) if case.expect_empty else (len(rows) > 0)
    vendor_ids = {row.get("vendor_id") for row in rows}
    source_files = {row.get("source_file") for row in rows}
    chunk_indices = {row.get("chunk_index") for row in rows}
    evidence_text = " ".join(str(row.get("chunk_text", "")).lower() for row in rows)
    metrics["vendor_match"] = (
        case.expect_empty
        or case.expected_vendor_id is None
        or case.expected_vendor_id in vendor_ids
    )
    metrics["source_match"] = (
        case.expected_source_file is None or case.expected_source_file in source_files
    )
    metrics["chunk_match"] = set(case.expected_chunk_indices).issubset(chunk_indices)
    metrics["keywords_match"] = all(keyword.lower() in evidence_text for keyword in case.expected_keywords)
    passed = all(
        value for key, value in metrics.items() if key != "row_count"
    )
    failure = None if passed else "Retrieved evidence did not satisfy the case expectations."
    return CaseResult(case.case_id, passed, metrics, failure)


def run_evaluation(
    cases: list[EvaluationCase],
    search_fn: Callable[[str, str | None], str | dict[str, Any]],
    mode: str,
    dataset: str,
) -> EvaluationReport:
    results = tuple(
        score_case(case, search_fn(case.question, case.expected_vendor_id))
        for case in cases
    )
    return EvaluationReport(
        dataset=dataset,
        mode=mode,
        created_at=datetime.now(timezone.utc).isoformat(),
        total_cases=len(results),
        passed_cases=sum(result.passed for result in results),
        results=results,
    )


def _mock_search(question: str, vendor_id: str | None):
    del question
    fixtures = {
        "VEND-789": (
            "Contract_VEND789_Gold.txt",
            "A 5% penalty per day applies after 4 business days.",
        ),
        "VEND-456": (
            "Contract_VEND456_Silver.txt",
            "A fixed fee of $500 per container per day will apply.",
        ),
        "VEND-123": (
            "Contract_VEND123_Bronze.txt",
            "Bronze Tier agreements do not include weather exemptions.",
        ),
    }
    source_file, chunk_text = fixtures.get(vendor_id, (None, None))
    if source_file is None:
        return {"ok": True, "tool": "search_vendor_contracts", "row_count": 0, "rows": []}
    return {
        "ok": True,
        "tool": "search_vendor_contracts",
        "row_count": 1,
        "rows": [{
            "source_file": source_file,
            "chunk_index": 0,
            "vendor_id": vendor_id,
            "chunk_text": chunk_text,
        }],
    }


def _live_search(question: str, vendor_id: str | None):
    from agent_server.data_tools import _search_vendor_contracts

    return _search_vendor_contracts(question, vendor_id=vendor_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="evaluation/baseline.jsonl")
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--output", type=Path, default=Path("evaluation/results.json"))
    parser.add_argument("--log-mlflow", action="store_true")
    args = parser.parse_args()

    cases = load_dataset(args.dataset)
    search_fn = _mock_search if args.mode == "mock" else _live_search
    report = run_evaluation(cases, search_fn, args.mode, args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report.as_dict(), indent=2) + "\n")
    if args.log_mlflow:
        import mlflow

        with mlflow.start_run(run_name=f"contract-agent-eval-{args.mode}"):
            mlflow.log_metric("eval.pass_rate", report.pass_rate)
            mlflow.log_metric("eval.total_cases", report.total_cases)
            mlflow.log_metric("eval.passed_cases", report.passed_cases)
            mlflow.log_dict(report.as_dict(), "evaluation_report.json")
    print(json.dumps({"pass_rate": report.pass_rate, "output": str(args.output)}))
    return 0 if report.pass_rate == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())