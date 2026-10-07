"""Responses API client for the existing read-only specialist App."""

import asyncio
import json
import logging
import re
from typing import Any
from decimal import Decimal

import httpx
from databricks.sdk import WorkspaceClient
from databricks_openai import AsyncDatabricksOpenAI

from agent_server.config import Settings
from agent_server.models import Citation, PenaltyEvidence, SpecialistResult

logger = logging.getLogger(__name__)

PENALTY_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "penalty_applicable": {"type": ["boolean", "null"]},
        "vendor_id": {"type": ["string", "null"]},
        "vendor_name": {"type": ["string", "null"]},
        "amount": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"]},
        "contract_basis": {"type": ["string", "null"]},
        "amount_quote": {"type": ["string", "null"]},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["source", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "penalty_applicable",
        "vendor_id",
        "vendor_name",
        "amount",
        "currency",
        "contract_basis",
        "amount_quote",
        "citations",
    ],
    "additionalProperties": False,
}


def _response_text(payload: dict[str, Any]) -> str:
    output: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if (
                isinstance(content, dict)
                and content.get("type") in {"output_text", "text"}
                and isinstance(content.get("text"), str)
            ):
                output.append(content["text"])
    return "\n".join(output).strip()


class SpecialistClient:
    def __init__(
        self,
        settings: Settings,
        workspace_client: WorkspaceClient,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._workspace_client = workspace_client
        self._http_client = http_client

    async def ask(self, question: str, conversation_id: str) -> SpecialistResult:
        if not self._settings.specialist_app_name:
            raise RuntimeError("SPECIALIST_APP_NAME is not configured.")

        app = await asyncio.to_thread(
            self._workspace_client.apps.get,
            self._settings.specialist_app_name,
        )
        app_url = getattr(app, "url", None)
        if not app_url:
            raise RuntimeError(
                f"The specialist App {self._settings.specialist_app_name!r} has no URL."
            )

        # This MVP uses the Boss App service principal. Forwarding the end-user
        # OAuth/OBO token is an explicit follow-up; do not imply user-level data access.
        auth_headers = self._workspace_client.config.authenticate()
        headers = dict(auth_headers)
        headers["Content-Type"] = "application/json"
        request_payload = {
            "input": [{"role": "user", "content": question}],
            "context": {"conversation_id": conversation_id},
            "stream": False,
        }
        url = f"{app_url.rstrip('/')}/api/responses"
        if self._http_client is None:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(url, headers=headers, json=request_payload)
        else:
            response = await self._http_client.post(
                url, headers=headers, json=request_payload
            )
        if response.is_error:
            logger.error(
                "Specialist App returned HTTP %s.", response.status_code
            )
            response.raise_for_status()
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("Specialist App returned invalid JSON.") from error
        if not isinstance(payload, dict):
            raise RuntimeError("Specialist App returned an unexpected response shape.")

        text = _response_text(payload)
        if not text:
            raise RuntimeError("Specialist App returned no answer text.")
        custom_outputs = payload.get("custom_outputs", {})
        return SpecialistResult(
            answer=text,
            custom_outputs=(
                custom_outputs if isinstance(custom_outputs, dict) else {}
            ),
        )


class PenaltyEvidenceExtractor:
    def __init__(
        self,
        settings: Settings,
        workspace_client: WorkspaceClient,
        client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or AsyncDatabricksOpenAI(
            workspace_client=workspace_client
        )

    async def extract(
        self, question: str, specialist: SpecialistResult
    ) -> PenaltyEvidence:
        existing = specialist.custom_outputs.get("penalty_evidence")
        if isinstance(existing, dict):
            evidence = PenaltyEvidence.model_validate(existing)
        else:
            result = await self._client.chat.completions.create(
                model=self._settings.model_endpoint,
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract only penalty facts explicitly stated in the "
                            "specialist answer. Do not infer or fill gaps. Return "
                            "null for unavailable facts. The amount_quote, "
                            "contract_basis, and each citation quote must be exact "
                            "substrings of the specialist answer."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "question": question,
                                "specialist_answer": specialist.answer,
                                "specialist_custom_outputs": specialist.custom_outputs,
                            }
                        ),
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "penalty_evidence",
                        "strict": True,
                        "schema": PENALTY_EXTRACTION_SCHEMA,
                    },
                },
            )
            content = result.choices[0].message.content
            if not isinstance(content, str):
                raise RuntimeError("Penalty evidence extraction returned no JSON.")
            evidence = PenaltyEvidence.model_validate_json(content)

        self._validate_quotes(evidence, specialist.answer)
        if evidence.vendor_id and not re.fullmatch(
            r"[A-Za-z0-9_-]{1,80}", evidence.vendor_id
        ):
            raise ValueError("The specialist returned an invalid vendor identifier.")
        return evidence

    @staticmethod
    def _validate_quotes(evidence: PenaltyEvidence, answer: str) -> None:
        if evidence.penalty_applicable is not True:
            return
        required_quotes = [evidence.contract_basis, evidence.amount_quote]
        required_quotes.extend(citation.quote for citation in evidence.citations)
        normalized_answer = answer.casefold()
        if any(
            not quote or quote.casefold() not in normalized_answer
            for quote in required_quotes
        ):
            raise ValueError(
                "Penalty evidence could not be verified against the specialist answer."
            )
        if evidence.vendor_id and evidence.vendor_id.casefold() not in normalized_answer:
            raise ValueError("Vendor ID was not present in the specialist answer.")
        if evidence.vendor_name and evidence.vendor_name.casefold() not in normalized_answer:
            raise ValueError("Vendor name was not present in the specialist answer.")
        if any(
            citation.source.casefold() not in normalized_answer
            for citation in evidence.citations
        ):
            raise ValueError("A penalty citation source was not present in the specialist answer.")
        amount_quote = (evidence.amount_quote or "").casefold()
        if evidence.amount is not None:
            numeric_tokens = re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", amount_quote)
            quoted_amounts = {
                Decimal(token.replace(",", "")) for token in numeric_tokens
            }
            if evidence.amount not in quoted_amounts:
                raise ValueError("Penalty amount does not match its quoted evidence.")
        if evidence.currency and evidence.currency.casefold() not in amount_quote:
            raise ValueError("Penalty currency does not match its quoted evidence.")
