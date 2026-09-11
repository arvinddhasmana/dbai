"""Evaluate contract retrieval evidence without scoring generated prose."""

from __future__ import annotations

import argparse
import json
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
    expected_chunk_ids: tuple[str, ...] = ()
    expected_keywords: tuple[str, ...] = ()
    required_facts: tuple[str, ...] = ()
    reference_answer: str | None = None
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


JUDGE_THRESHOLDS = {
    "groundedness": 0.8,
    "completeness": 0.8,
    "citation_correctness": 0.8,
}


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
                    expected_chunk_ids=tuple(raw.get("expected_chunk_ids", [])),
                    expected_keywords=tuple(raw.get("expected_keywords", [])),
                    required_facts=tuple(raw.get("required_facts", [])),
                    reference_answer=raw.get("reference_answer"),
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
    retrieval = retrieval_metrics(case, rows)
    metrics.update(retrieval)
    required_checks = [
        metrics["ok_match"],
        metrics["empty_match"],
        metrics["vendor_match"],
        metrics["source_match"],
        metrics["chunk_match"],
        metrics["keywords_match"],
    ]
    if case.expected_chunk_ids:
        required_checks.append(
            set(case.expected_chunk_ids).issubset(
                {row.get("chunk_id") for row in rows}
            )
        )
    if case.required_facts:
        required_checks.append(retrieval["required_fact_coverage"] == 1.0)
    passed = all(required_checks)
    failure = None if passed else "Retrieved evidence did not satisfy the case expectations."
    return CaseResult(case.case_id, passed, metrics, failure)


def apply_judge_quality(
    case: EvaluationCase,
    result: CaseResult,
    answer: str,
    judged: Any,
) -> CaseResult:
    """Apply bounded answer-quality thresholds to a retrieval result."""
    metrics = dict(result.metrics)
    for field in (*JUDGE_THRESHOLDS, "unsupported_claims", "confidence"):
        value = judged.get(field) if isinstance(judged, dict) else getattr(judged, field)
        metrics[f"judge_{field}"] = float(value)

    expected_facts = case.required_facts or case.expected_keywords
    answer_text = answer.lower()
    metrics["answer_fact_coverage"] = (
        sum(fact.lower() in answer_text for fact in expected_facts) / len(expected_facts)
        if expected_facts else 1.0
    )
    quality_checks = [
        metrics[f"judge_{field}"] >= threshold
        for field, threshold in JUDGE_THRESHOLDS.items()
    ]
    quality_checks.append(metrics["judge_unsupported_claims"] == 0.0)
    if expected_facts:
        quality_checks.append(metrics["answer_fact_coverage"] == 1.0)
    quality_passed = all(quality_checks)
    metrics["answer_quality_passed"] = quality_passed
    passed = result.passed and quality_passed
    failure = result.failure
    if not quality_passed:
        failure = "Generated answer did not satisfy answer-quality thresholds."
    return CaseResult(case.case_id, passed, metrics, failure)


def retrieval_metrics(case: EvaluationCase, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate deterministic retrieval metrics from ranked search rows."""
    expected_ids = set(case.expected_chunk_ids)
    expected_indices = set(case.expected_chunk_indices)

    def is_relevant(row: dict[str, Any]) -> bool:
        if expected_ids:
            return row.get("chunk_id") in expected_ids
        if expected_indices:
            return (
                row.get("source_file") == case.expected_source_file
                and row.get("chunk_index") in expected_indices
            )
        if case.expected_source_file:
            return row.get("source_file") == case.expected_source_file
        if case.expected_vendor_id:
            return row.get("vendor_id") == case.expected_vendor_id
        return False

    relevant_flags = [is_relevant(row) for row in rows]
    relevant_count = sum(relevant_flags)
    expected_count = len(expected_ids or expected_indices or ({case.expected_source_file} if case.expected_source_file else set()))
    precision = relevant_count / len(rows) if rows else 0.0
    recall = min(relevant_count / expected_count, 1.0) if expected_count else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    first_rank = next((index + 1 for index, relevant in enumerate(relevant_flags) if relevant), None)
    fact_text = " ".join(str(row.get("chunk_text", "")).lower() for row in rows)
    fact_coverage = (
        sum(fact.lower() in fact_text for fact in case.required_facts) / len(case.required_facts)
        if case.required_facts else 1.0
    )
    return {
        "retrieval_precision": precision,
        "retrieval_recall": recall,
        "retrieval_f1": f1,
        "first_relevant_rank": first_rank,
        "required_fact_coverage": fact_coverage,
        "irrelevant_context_rate": 1.0 - precision if rows else 0.0,
    }


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
    fixtures = {
        "VEND-789": {
            "delay": "A 5% penalty per day applies after 4 business days.",
            "payment": "Payment obligations and invoices remain due under the agreement.",
            "delivery": "Gold Tier requires on-time delivery and electronic proof of delivery.",
            "on-time": "Gold Tier performance requires an on-time delivery rate of at least 96 percent each calendar month.",
        },
        "VEND-456": {
            "delay": "A fixed fee of $500 per container per day will apply.",
            "payment": "Payment obligations and invoices remain due under the agreement.",
            "delivery": "Silver Tier includes delivery appointments and electronic proof of delivery.",
        },
        "VEND-123": {
            "weather": "Bronze Tier agreements do not include weather exemptions.",
            "payment": "Payment obligations and invoices remain due under the agreement.",
            "delivery": "Bronze Tier requires on-time delivery and electronic proof of delivery.",
        },
    }
    vendor_fixtures = fixtures.get(vendor_id)
    if vendor_fixtures is None:
        return {"ok": True, "tool": "search_vendor_contracts", "row_count": 0, "rows": []}
    question_text = question.lower()
    topic = "on-time" if "on-time" in question_text and "on-time" in vendor_fixtures else next(
        (key for key in sorted(vendor_fixtures, key=len, reverse=True) if key in question_text),
        next(iter(vendor_fixtures)),
    )
    source_file = f"Contract_{vendor_id.replace('-', '')}_{'Gold' if vendor_id == 'VEND-789' else 'Silver' if vendor_id == 'VEND-456' else 'Bronze'}.txt"
    return {
        "ok": True,
        "tool": "search_vendor_contracts",
        "row_count": 1,
        "rows": [{
            "source_file": source_file,
            "chunk_index": 0,
            "vendor_id": vendor_id,
            "chunk_text": vendor_fixtures[topic],
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
        from evaluation.mlflow_logging import log_evaluation_report

        log_evaluation_report(report, report_path=args.output)
    print(json.dumps({"pass_rate": report.pass_rate, "output": str(args.output)}))
    return 0 if report.pass_rate == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())