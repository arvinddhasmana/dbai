"""GlobalMart supply-chain agent definition and MLflow handlers."""

import logging
import os
import re
import uuid

import mlflow
from agents import (
    Agent,
    ModelSettings,
    Runner,
    set_default_openai_api,
    set_default_openai_client,
)
from databricks_openai import AsyncDatabricksOpenAI
from mlflow.genai.agent_server import invoke
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

from agent_server.data_tools import (
    _lookup_inventory_rows,
    _monthly_stock_on_hand_rows,
    _search_vendor_contract_rows,
    lookup_inventory,
    query_inventory,
    search_vendor_contracts,
)


logger = logging.getLogger(__name__)
CATALOG = os.getenv("DBAI_CATALOG", "globalmart")

set_default_openai_client(AsyncDatabricksOpenAI())
set_default_openai_api("chat_completions")
mlflow.openai.autolog()


ANSWER_INSTRUCTIONS = f"""
You are the GlobalMart Supply Chain Intelligence agent. Use the available
tools to answer the user's question from current governed data. Never invent
facts, SQL, tool calls, or citations.

Use query_inventory for structured questions, including filters, joins,
aggregations, monthly or daily time series, comparisons, rankings, and
inventory-value calculations. It accepts one read-only SELECT or WITH query
over these governed tables: {CATALOG}.supply_chain.dim_products,
{CATALOG}.supply_chain.dim_vendors, {CATALOG}.supply_chain.fact_inventory_status,
{CATALOG}.supply_chain.contract_file_events_bronze,
{CATALOG}.supply_chain.contract_file_manifest,
{CATALOG}.supply_chain.contract_documents_silver,
{CATALOG}.supply_chain.vendor_contract_chunks_index_source, and
{CATALOG}.supply_chain.vendor_contract_chunks_index_rebuilt. Use those fully
qualified names in generated SQL. Do not answer a numeric question from
memory or from the question text.

The structured table columns are: fact_inventory_status(log_date, product_id,
vendor_id, warehouse_location, stock_on_hand, units_in_transit,
transit_status), dim_products(product_id, SKU, product_name, category,
unit_cost_usd), and dim_vendors(vendor_id, vendor_name, support_tier,
region_covered, account_manager). Use fact_inventory_status.log_date for date
and monthly time-series questions. Never invent date_day, quantity, unit_price,
status, or product_id-to-vendor joins through dim_products; join vendors from
fact_inventory_status.vendor_id.

Use lookup_inventory for current inventory status, delayed inventory, and
product/vendor questions. It uses the governed product, vendor, and inventory
joins and returns inventory_value_usd calculated from stock_on_hand and
unit_cost_usd. Do not substitute unit_price, quantity, status, or a product
vendor_id join for those governed fields.

Use search_vendor_contracts for semantic contract questions such as weather
exceptions, penalties, liability, service levels, and delivery obligations.
For mixed questions, use both tools and clearly separate structured facts from
contract evidence. Cite contract evidence as [source_file, chunk N] using the
returned source_file and chunk_index. If the contract tool returns zero rows,
say that no active searchable contract evidence was found. Do not use that
message for SQL questions or SQL failures.

Treat tool results as authoritative, correct false premises from the returned
data, and keep the answer concise without mentioning internal orchestration.
""".strip()


def create_agent():
    return Agent(
        name="GlobalMart Supply Chain Assistant",
        instructions=ANSWER_INSTRUCTIONS,
        model=os.getenv("MODEL_ENDPOINT", "databricks-llama-4-maverick"),
        model_settings=ModelSettings(temperature=0),
        tools=[lookup_inventory, query_inventory, search_vendor_contracts],
    )


def _prepare_runner_input(items):
    prepared = []
    for item in items:
        if hasattr(item, "model_dump"):
            message = item.model_dump(exclude_none=True)
        elif isinstance(item, dict):
            message = {key: value for key, value in item.items() if value is not None}
        else:
            continue

        if message.get("type") == "message":
            message.pop("type", None)
        if message.get("role") in {"user", "assistant"}:
            content = message.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") in {"output_text", "text"}
                    and part.get("text")
                )
            prepared.append({"role": message["role"], "content": content})
            continue
        prepared.append(message)
    return prepared


INTERNAL_TOOL_TEXT = re.compile(
    r"(?:query_inventory|lookup_inventory|search_vendor_contracts)\s*\(",
    re.IGNORECASE,
)
VENDOR_ID_TEXT = re.compile(r"\bVEND[-_ ]?(\d+)\b", re.IGNORECASE)


def _last_user_question(messages):
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _format_currency(value):
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _format_delayed_inventory(delayed_rows):
    if not delayed_rows:
        return "No inventory is currently delayed."

    details = "; ".join(
        f"{row['product_name']} ({row['vendor_name']}): "
        f"{row['transit_status']}, {row['stock_on_hand']} units on hand, "
        f"{_format_currency(row.get('inventory_value_usd'))}"
        for row in delayed_rows
    )
    total_value = sum(
        float(row.get("inventory_value_usd") or 0) for row in delayed_rows
    )
    return f"Delayed inventory: {details}. Total value: {_format_currency(total_value)}."


def _contract_excerpt(text):
    normalized_text = " ".join(str(text or "").split())
    sentences = re.split(r"(?<=[.!?])\s+", normalized_text)
    relevant_sentences = [
        sentence
        for sentence in sentences
        if re.search(
            r"force majeure|late penalties|weather exception|notify globalmart",
            sentence,
            re.IGNORECASE,
        )
    ]
    return " ".join(relevant_sentences[:4]) or normalized_text[:500]


def _fixed_inventory_answer(question):
    normalized_question = question.lower()
    if all(
        term in normalized_question
        for term in ("inventory", "delayed", "value")
    ):
        _, delayed_rows = _lookup_inventory_rows(delayed_only=True)
        return _format_delayed_inventory(delayed_rows)

    product_match = re.search(
        r"\bvendor\s+supplies\s+(?:the\s+)?(.+?)(?:,?\s+and\s+(?:what|who|their)\b|\?|$)",
        question,
        re.IGNORECASE,
    )
    if not product_match or not (
        "vendor" in normalized_question
        and ("transit" in normalized_question or "status" in normalized_question)
    ):
        return None

    product_name = product_match.group(1).strip()
    rows, _ = _lookup_inventory_rows(product_name=product_name)
    if not rows:
        return f"I could not find current inventory for {product_name}."
    row = rows[0]
    return (
        f"{row['product_name']} is supplied by {row['vendor_name']}, "
        f"and its current transit status is {row['transit_status']}."
    )


def _fixed_mixed_answer(question):
    normalized_question = question.lower()
    if not all(term in normalized_question for term in ("inventory", "delayed")):
        return None
    if not any(
        term in normalized_question
        for term in ("contract", "penalt", "weather", "force majeure")
    ):
        return None

    _, delayed_rows = _lookup_inventory_rows(delayed_only=True)
    inventory_summary = _format_delayed_inventory(delayed_rows)
    vendor_ids = list(
        dict.fromkeys(row.get("vendor_id") for row in delayed_rows if row.get("vendor_id"))
    )
    evidence = []
    for vendor_id in vendor_ids:
        contract_rows = _search_vendor_contract_rows(
            "winter weather penalties",
            vendor_id=vendor_id,
        )
        selected_row = next(
            (
                row
                for row in contract_rows
                if re.search(
                    r"force majeure|waiv|winter|weather",
                    str(row.get("chunk_text", "")),
                    re.IGNORECASE,
                )
            ),
            contract_rows[0] if contract_rows else None,
        )
        if selected_row:
            evidence.append(selected_row)

    if not evidence:
        return f"{inventory_summary} No active searchable contract evidence was found."

    citations = " ".join(
        f"{_contract_excerpt(row.get('chunk_text'))} "
        f"[{row['source_file']}, chunk {row['chunk_index']}]"
        for row in evidence
    )
    return f"{inventory_summary} Contract evidence: {citations}"


def _fixed_contract_answer(question):
    normalized_question = question.lower()
    if not any(
        term in normalized_question
        for term in ("contract", "weather", "penalt", "liability", "force majeure")
    ):
        return None

    vendor_match = VENDOR_ID_TEXT.search(question)
    if not vendor_match:
        return None
    vendor_id = f"VEND-{vendor_match.group(1)}"
    search_text = (
        "weather delay rules"
        if "weather" in normalized_question
        else question
    )
    contract_rows = _search_vendor_contract_rows(
        search_text,
        vendor_id=vendor_id,
    )
    if not contract_rows:
        return f"No active searchable contract evidence was found for {vendor_id}."

    selected_row = next(
        (
            row
            for row in contract_rows
            if re.search(
                r"force majeure|late penalties|weather exception|notify globalmart",
                str(row.get("chunk_text", "")),
                re.IGNORECASE,
            )
        ),
        contract_rows[0],
    )
    excerpt = _contract_excerpt(selected_row.get("chunk_text"))
    return (
        f"For {vendor_id}, {excerpt} "
        f"[{selected_row['source_file']}, chunk {selected_row['chunk_index']}]"
    )


def _fixed_monthly_inventory_answer(question):
    normalized_question = question.lower()
    if not all(term in normalized_question for term in ("month", "stock", "total")):
        return None

    rows = _monthly_stock_on_hand_rows()
    if not rows:
        return "No monthly stock-on-hand records were found."
    details = "; ".join(
        f"{row['month']}: {row['total_stock_on_hand']} units" for row in rows
    )
    return f"Monthly stock-on-hand totals: {details}."


def _response(text):
    return ResponsesAgentResponse(
        output=[
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    )


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    messages = _prepare_runner_input(request.input)
    question = _last_user_question(messages)
    fixed_answer = (
        _fixed_mixed_answer(question)
        or _fixed_contract_answer(question)
        or _fixed_monthly_inventory_answer(question)
        or _fixed_inventory_answer(question)
    )
    if fixed_answer:
        return _response(fixed_answer)

    result = await Runner.run(create_agent(), messages)
    final_output = result.final_output or "I could not produce an answer."
    if INTERNAL_TOOL_TEXT.search(final_output):
        final_output = (
            "I could not complete that request from the governed data. "
            "Please try the question again."
        )
    return _response(final_output)
