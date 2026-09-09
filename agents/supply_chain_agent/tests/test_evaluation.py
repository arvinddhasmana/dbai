import json
from pathlib import Path
from types import SimpleNamespace

from evaluation.runner import EvaluationReport, load_dataset, run_evaluation, score_case
from evaluation.judge import build_prompt, parse_result
from evaluation.validate_corpus import validate_cases, validate_live_rows
from evaluation.runner import EvaluationCase
from evaluation.adapters import AgentEvaluationResult, _agent_invocation_url, invoke_agent, judge_agent_result
from evaluation.calibration import load_calibration, validate_judge_result
from evaluation.mlflow_logging import log_evaluation_report, log_genai_evaluation


DATASET = Path(__file__).parents[1] / "evaluation" / "baseline.jsonl"
CALIBRATION = Path(__file__).parents[1] / "evaluation" / "calibration.jsonl"


def test_baseline_dataset_loads_with_stable_cases():
    cases = load_dataset(DATASET)
    assert [case.case_id for case in cases] == [
        "vend789-delay",
        "vend456-delay",
        "vend123-weather",
        "unknown-vendor",
        "vend123-payment",
        "vend123-delivery",
        "vend456-payment",
        "vend456-delivery",
        "vend789-service-level",
        "vend789-delivery",
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
    case = next(case for case in load_dataset(DATASET) if case.case_id == "unknown-vendor")
    result = score_case(case, {"ok": True, "rows": []})
    assert result.passed is True


def test_mock_runner_is_deterministic_and_does_not_call_databricks():
    cases = [case for case in load_dataset(DATASET) if case.case_id == "unknown-vendor"]
    calls = []

    def fake_search(question, vendor_id):
        calls.append((question, vendor_id))
        return {"ok": True, "rows": []}

    report = run_evaluation(cases, fake_search, "mock", str(DATASET))
    assert report.pass_rate == 1.0
    assert calls == [("What are the contract terms for VEND-999?", "VEND-999")]
    assert json.loads(json.dumps(report.as_dict()))["passed_cases"] == 1


def test_retrieval_metrics_are_reported_without_changing_legacy_pass_fail():
    case = load_dataset(DATASET)[0]
    result = score_case(case, {
        "ok": True,
        "rows": [{
            "source_file": "Contract_VEND789_Gold.txt",
            "chunk_index": 0,
            "vendor_id": "VEND-789",
            "chunk_text": "A 5% penalty per day applies after 4 business days.",
        }],
    })
    assert result.passed is True
    assert result.metrics["retrieval_precision"] == 1.0
    assert result.metrics["retrieval_recall"] == 1.0


def test_judge_contract_requires_bounded_scores_and_rationale():
    prompt = build_prompt(
        "What is the delay penalty?",
        "The penalty is 5% per day.",
        [{"source_file": "contract.txt", "chunk_index": 0, "chunk_text": "5% per day."}],
        ["5% per day"],
    )
    assert "Prompt version: groundedness-v1" in prompt
    result = parse_result({
        "context_precision": 1,
        "context_recall": 1,
        "groundedness": 1,
        "completeness": 1,
        "citation_correctness": 1,
        "unsupported_claims": 0,
        "rationale": "The answer is supported.",
        "confidence": 0.9,
    })
    assert result.groundedness == 1.0


def test_corpus_validator_checks_structure_and_live_annotations():
    case = EvaluationCase(
        case_id="annotated",
        question="What is the fee?",
        expected_vendor_id="VEND-456",
        expected_source_file="contract.txt",
        expected_chunk_ids=("chunk-1",),
        required_facts=("$500",),
    )
    assert validate_cases([case]) == []
    assert validate_live_rows(case, [{
        "chunk_id": "chunk-1",
        "source_file": "contract.txt",
        "chunk_text": "A fixed fee of $500 applies.",
    }]) == []
    issues = validate_live_rows(case, [])
    assert any("missing chunk ids" in issue.message for issue in issues)


def test_corpus_validator_rejects_requirements_on_empty_cases():
    case = EvaluationCase(
        case_id="empty",
        question="Unknown",
        expect_empty=True,
        required_facts=("fact",),
    )
    issues = validate_cases([case])
    assert [issue.message for issue in issues] == [
        "empty-result cases cannot require chunks or facts"
    ]


def test_agent_adapter_extracts_answer_and_contract_evidence():
    case = EvaluationCase(case_id="adapter", question="What is the fee?")
    requests = []

    def fake_invoker(request):
        requests.append(request)
        return {
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "The fee is $500."}],
            }],
            "custom_outputs": {
                "contract_evidence": {"ok": True, "rows": [{"chunk_text": "$500"}]}
            },
        }

    result = invoke_agent(case, fake_invoker)
    assert result.answer == "The fee is $500."
    assert result.evidence["rows"][0]["chunk_text"] == "$500"
    assert requests == [{"input": [{"role": "user", "content": "What is the fee?"}]}]


def test_agent_invocation_url_uses_mlflow_agent_server_route():
    assert _agent_invocation_url("https://app.example.com") == "https://app.example.com/invocations"
    assert _agent_invocation_url("https://app.example.com/invocations") == "https://app.example.com/invocations"


def test_judge_adapter_sends_structured_json_request():
    case = EvaluationCase(case_id="judge", question="What is the fee?", required_facts=("$500",))
    result = AgentEvaluationResult(
        answer="The fee is $500.",
        evidence={"rows": [{"source_file": "contract.txt", "chunk_index": 0, "chunk_text": "$500"}]},
    )
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": json.dumps({
                "context_precision": 1,
                "context_recall": 1,
                "groundedness": 1,
                "completeness": 1,
                "citation_correctness": 1,
                "unsupported_claims": 0,
                "rationale": "Supported by the retrieved chunk.",
                "confidence": 0.9,
            })}}]}

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    judged = judge_agent_result(case, result, FakeClient(), "judge-endpoint")
    assert judged.groundedness == 1.0
    assert calls[0]["model"] == "judge-endpoint"
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_calibration_fixtures_load_and_apply_thresholds():
    cases = load_calibration(CALIBRATION)
    assert [case.case_id for case in cases] == [
        "grounded_answer",
        "incomplete_answer",
        "unsupported_answer",
    ]
    grounded = SimpleNamespace(groundedness=0.95, completeness=0.95, unsupported_claims=0)
    assert validate_judge_result(cases[0], grounded) == []
    incomplete = SimpleNamespace(groundedness=0.9, completeness=0.7, unsupported_claims=0)
    assert validate_judge_result(cases[1], incomplete) == []


def test_calibration_reports_threshold_failures_and_rejects_unknown_thresholds():
    cases = load_calibration(CALIBRATION)
    unsupported = SimpleNamespace(groundedness=0.9, unsupported_claims=0)
    failures = validate_judge_result(cases[2], unsupported)
    assert "groundedness above maximum 0.8" in failures
    assert "unsupported_claims below minimum 1.0" in failures
    invalid = cases[0].__class__(
        case_id="invalid",
        question="q",
        answer="a",
        contexts=(),
        required_facts=(),
        expected={"unknown_min": 0.5},
    )
    try:
        validate_judge_result(invalid, SimpleNamespace(unknown=1))
    except ValueError as error:
        assert "Unsupported calibration threshold" in str(error)
    else:
        raise AssertionError("Expected unknown calibration threshold to fail")


def test_mlflow_logging_records_metrics_without_raw_evaluation_inputs():
    case = load_dataset(DATASET)[0]
    report = run_evaluation(
        [case],
        lambda question, vendor_id: {
            "ok": True,
            "rows": [{
                "vendor_id": vendor_id,
                "source_file": case.expected_source_file,
                "chunk_index": 0,
                "chunk_text": "A 5% penalty per day applies after 4 business days.",
            }],
        },
        "mock",
        str(DATASET),
    )

    calls = {"params": [], "metrics": [], "tags": [], "artifacts": [], "dicts": []}

    class FakeRun:
        info = type("Info", (), {"run_id": "run-123"})()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeMlflow:
        def start_run(self, **kwargs):
            return FakeRun()

        def set_tags(self, values):
            calls["tags"].append(values)

        def log_params(self, values):
            calls["params"].append(values)

        def log_metrics(self, values):
            calls["metrics"].append(values)

        def log_artifact(self, *args, **kwargs):
            calls["artifacts"].append((args, kwargs))

        def log_dict(self, *args, **kwargs):
            calls["dicts"].append((args, kwargs))

    assert log_evaluation_report(
        report,
        aggregate_metrics={"retrieval_f1_mean": 1.0},
        mlflow_module=FakeMlflow(),
    ) == "run-123"
    assert calls["params"] == [{"evaluation_total_cases": 1, "evaluation_passed_cases": 1}]
    assert any("evaluation_pass_rate" in metrics for metrics in calls["metrics"])
    assert any("retrieval_f1_mean" in metrics for metrics in calls["metrics"])
    logged_text = json.dumps(calls)
    assert case.question not in logged_text
    assert "5% penalty" not in logged_text


def test_genai_logging_publishes_experiment_evaluation_scores():
    calls = []

    class FakeScorer:
        def __init__(self, name):
            self.name = name

        def register(self, **kwargs):
            return self

    class FakeGenai:
        def evaluate(self, **kwargs):
            calls.append(kwargs)
            return "evaluation-result"

    class FakeMlflow:
        genai = FakeGenai()

        def set_experiment(self, **kwargs):
            return None

    rows = [{
        "inputs": {"question": "What is the fee?"},
        "outputs": {
            "passed": True,
            "metrics": {"retrieval_f1": 1.0, "required_fact_coverage": 1.0},
            "judge": {"groundedness": 1.0, "completeness": 1.0},
        },
        "expectations": {"required_facts": ["$500"]},
    }]

    assert log_genai_evaluation(rows, mlflow_module=FakeMlflow()) == "evaluation-result"
    assert calls[0]["data"] == rows
    assert [scorer.name for scorer in calls[0]["scorers"]] == [
        "judge_groundedness",
        "judge_completeness",
    ]
    assert all(scorer.aggregations == [] for scorer in calls[0]["scorers"])


def test_report_logs_genai_evaluation_inside_the_single_active_run():
    calls = []

    class FakeRun:
        info = type("Info", (), {"run_id": "run-123"})()

        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *args):
            calls.append("exit")
            return False

    class FakeGenai:
        def evaluate(self, **kwargs):
            calls.append("evaluate")
            return "evaluation-result"

    class FakeMlflow:
        genai = FakeGenai()

        def start_run(self, **kwargs):
            calls.append("start")
            return FakeRun()

        def set_tags(self, values):
            pass

        def log_params(self, values):
            pass

        def log_metrics(self, values):
            pass

        def log_artifact(self, *args, **kwargs):
            pass

        def log_dict(self, *args, **kwargs):
            pass

    report = EvaluationReport(
        dataset="dataset.jsonl",
        mode="live",
        created_at="2026-01-01T00:00:00+00:00",
        total_cases=1,
        passed_cases=1,
        results=(),
    )
    assert log_evaluation_report(
        report,
        aggregate_metrics={"retrieval_f1_mean": 1.0},
        genai_rows=[{"inputs": {}, "outputs": {}}],
        mlflow_module=FakeMlflow(),
    ) == "run-123"
    assert calls == ["start", "enter", "evaluate", "exit"]


def test_job_uses_databricks_tracking_uri_for_profile(monkeypatch):
    import evaluation.job as job

    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    assert job._configure_mlflow_tracking(None, "dbai-dev") == "databricks://dbai-dev"
    assert job.os.environ["MLFLOW_TRACKING_URI"] == "databricks://dbai-dev"
    assert job.os.environ["DATABRICKS_CONFIG_PROFILE"] == "dbai-dev"
    assert job._configure_mlflow_tracking("databricks", "dbai-dev") == "databricks"