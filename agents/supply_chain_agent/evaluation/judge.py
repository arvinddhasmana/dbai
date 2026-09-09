"""Low-cost, model-agnostic judge contract for RAG evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PROMPT_VERSION = "groundedness-v1"


@dataclass(frozen=True)
class JudgeResult:
    context_precision: float
    context_recall: float
    groundedness: float
    completeness: float
    citation_correctness: float
    unsupported_claims: int
    rationale: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_precision": self.context_precision,
            "context_recall": self.context_recall,
            "groundedness": self.groundedness,
            "completeness": self.completeness,
            "citation_correctness": self.citation_correctness,
            "unsupported_claims": self.unsupported_claims,
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


def build_prompt(question: str, answer: str, contexts: list[dict[str, Any]], required_facts: list[str]) -> str:
    """Build a bounded judge prompt with only evaluation inputs."""
    context_payload = [
        {
            "source_file": context.get("source_file"),
            "chunk_index": context.get("chunk_index"),
            "chunk_text": context.get("chunk_text", ""),
        }
        for context in contexts
    ]
    return (
        f"Prompt version: {PROMPT_VERSION}\n"
        "You are evaluating a contract RAG answer. Return JSON only.\n"
        "Score each 0.0 to 1.0. Count unsupported factual claims.\n"
        "Judge only against the supplied contexts and required facts.\n\n"
        f"Question:\n{question}\n\n"
        f"Retrieved contexts:\n{json.dumps(context_payload, ensure_ascii=True)}\n\n"
        f"Required facts:\n{json.dumps(required_facts, ensure_ascii=True)}\n\n"
        f"Answer:\n{answer}\n\n"
        "JSON schema:\n"
        '{"context_precision":0.0,"context_recall":0.0,"groundedness":0.0,'
        '"completeness":0.0,"citation_correctness":0.0,"unsupported_claims":0,'
        '"rationale":"...","confidence":0.0}'
    )


def parse_result(payload: str | dict[str, Any]) -> JudgeResult:
    """Parse and validate strict judge output before aggregation."""
    raw = json.loads(payload) if isinstance(payload, str) else payload
    required = (
        "context_precision",
        "context_recall",
        "groundedness",
        "completeness",
        "citation_correctness",
        "unsupported_claims",
        "rationale",
        "confidence",
    )
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"Judge result is missing fields: {', '.join(missing)}")

    scores = {
        key: float(raw[key])
        for key in required
        if key not in {"unsupported_claims", "rationale"}
    }
    if any(value < 0.0 or value > 1.0 for value in scores.values()):
        raise ValueError("Judge scores must be between 0.0 and 1.0")
    unsupported_claims = int(raw["unsupported_claims"])
    if unsupported_claims < 0:
        raise ValueError("unsupported_claims must be non-negative")
    if not isinstance(raw["rationale"], str) or not raw["rationale"].strip():
        raise ValueError("Judge rationale must be a non-empty string")
    return JudgeResult(
        context_precision=scores["context_precision"],
        context_recall=scores["context_recall"],
        groundedness=scores["groundedness"],
        completeness=scores["completeness"],
        citation_correctness=scores["citation_correctness"],
        unsupported_claims=unsupported_claims,
        rationale=raw["rationale"],
        confidence=scores["confidence"],
    )
