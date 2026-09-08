import json
from pathlib import Path

from evaluation.runner import load_dataset, run_evaluation, score_case


DATASET = Path(__file__).parents[1] / "evaluation" / "baseline.jsonl"


def test_baseline_dataset_loads_with_stable_cases():
    cases = load_dataset(DATASET)
    assert [case.case_id for case in cases] == [
        "vend789-delay",
        "vend456-delay",
        "vend123-weather",
        "unknown-vendor",
    ]


def test_score_case_requires_grounded_evidence():
    case = load_dataset(DATASET)[0]
    result = score_case(case, {
        "ok": True,
        "rows": [{
            "vendor_id": "VEND-789",
            "source_file": "Contract_VEND789_Gold.txt",
            "chunk_index": 1,
            "chunk_text": "A 5% penalty per day applies after 4 business days.",
        }],
    })
    assert result.passed is True


def test_score_case_distinguishes_empty_success_from_failure():
    case = load_dataset(DATASET)[-1]
    result = score_case(case, {"ok": True, "rows": []})
    assert result.passed is True


def test_mock_runner_is_deterministic_and_does_not_call_databricks():
    cases = load_dataset(DATASET)[-1:]
    calls = []

    def fake_search(question, vendor_id):
        calls.append((question, vendor_id))
        return {"ok": True, "rows": []}

    report = run_evaluation(cases, fake_search, "mock", str(DATASET))
    assert report.pass_rate == 1.0
    assert calls == [("What are the contract terms for VEND-999?", "VEND-999")]
    assert json.loads(json.dumps(report.as_dict()))["passed_cases"] == 1