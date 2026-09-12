from common_utils.evaluation.adapters import extract_answer, observation_from_response
from common_utils.evaluation.models import AgentObservation, EvaluationCase
from common_utils.evaluation.scorers import required_fact_coverage, tool_routing_accuracy


def test_case_expectations_are_stable():
    case = EvaluationCase(
        case_id="hybrid",
        question="How do contract and inventory facts compare?",
        scenario="hybrid",
        required_facts=("monthly cycle count",),
        expected_tool_families=("genie", "vector_search"),
    )

    assert case.expectations() == {
        "case_id": "hybrid",
        "scenario": "hybrid",
        "required_facts": ["monthly cycle count"],
        "expected_tool_families": ["genie", "vector_search"],
    }


def test_observation_parses_sanitized_supervisor_metadata():
    observation = observation_from_response(
        {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "The answer."}],
                }
            ],
            "custom_outputs": {
                "evaluation": {
                    "tool_families": ["genie", "vector_search"],
                    "tool_calls": [{"family": "genie", "name": "ask"}],
                }
            },
        }
    )

    assert observation.answer == "The answer."
    assert observation.tool_families == ("genie", "vector_search")
    assert observation.outputs()["evaluation"]["tool_calls"] == [
        {"family": "genie", "name": "ask"}
    ]


def test_custom_scorers_cover_facts_and_required_tool_families():
    outputs = AgentObservation(
        answer="The monthly cycle count applies.",
        tool_families=("vector_search", "genie"),
    ).outputs()
    expectations = {
        "required_facts": ["monthly cycle count"],
        "expected_tool_families": ["vector_search", "genie"],
    }

    assert required_fact_coverage(outputs, expectations) == 1.0
    assert tool_routing_accuracy(outputs, expectations) == 1.0


def test_extract_answer_supports_direct_output_text():
    assert extract_answer({"output": [{"text": "Direct text"}]}) == "Direct text"