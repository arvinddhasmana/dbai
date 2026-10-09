"""Retrieve contract evidence through the governed Unity Catalog SQL function.

The App intentionally has one retrieval capability: the managed AI Search index
is exposed through ``search_vendor_contracts`` and invoked with the signed-in
user's workspace client. Query text is SQL-literal escaped because the function
call is assembled as SQL; identifiers come only from App configuration.
"""

import json
import logging
import os
import time

from agents import function_tool

from agent_server.utils import get_user_workspace_client
from agent_server.observability import start_retriever_span


logger = logging.getLogger(__name__)

CATALOG = os.getenv("DBAI_CATALOG", "globalmart")
CONTRACT_SEARCH_FUNCTION = f"{CATALOG}.supply_chain.search_vendor_contracts"
MAX_SEARCH_RESULTS = 10


def _warehouse_id():
    """Return the configured SQL warehouse ID or fail before issuing a query."""
    warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
    if not warehouse_id:
        raise RuntimeError("DATABRICKS_SQL_WAREHOUSE_ID is not configured for this app.")
    return warehouse_id


def _sql_literal(value):
    """Encode a nullable value as a SQL literal, escaping embedded apostrophes."""
    if value is None or not str(value).strip():
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _execute_sql(statement):
    """Execute a SQL statement, poll to completion, and map rows by column name.

    Databricks' statement-execution SDK is synchronous, so callers run this
    function in a worker thread rather than blocking the async agent loop.
    """
    client = get_user_workspace_client()
    response = client.statement_execution.execute_statement(
        statement,
        warehouse_id=_warehouse_id(),
        wait_timeout="30s",
    )

    def state(result):
        """Normalize the SDK's enum/string state for the completion loop."""
        value = result.status.state
        return getattr(value, "value", str(value).rsplit(".", 1)[-1])

    while state(response) in {"PENDING", "RUNNING"}:
        time.sleep(1)
        response = client.statement_execution.get_statement(response.statement_id)

    final_state = state(response)
    if final_state != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or str(error) or "unknown SQL error"
        raise RuntimeError(f"Databricks SQL failed ({final_state}): {message}")

    result = getattr(response, "result", None)
    manifest = getattr(response, "manifest", None)
    columns = [
        getattr(column, "name", f"column_{index}")
        for index, column in enumerate(
            getattr(getattr(manifest, "schema", None), "columns", []) or []
        )
    ]
    rows = getattr(result, "data_array", None) or []
    return [dict(zip(columns, row)) for row in rows]


def _search_vendor_contract_rows(search_text, vendor_id=None, support_tier=None, region=None):
    """Call the governed search function and return its ranked evidence rows."""
    statement = f"""
SELECT
  source_file,
  chunk_index,
  vendor_id,
  vendor_name,
  support_tier,
  region_covered,
  chunk_text,
  score
FROM {CONTRACT_SEARCH_FUNCTION}(
  {_sql_literal(search_text)},
  {_sql_literal(vendor_id)},
  {_sql_literal(support_tier)},
  {_sql_literal(region)}
)
ORDER BY score DESC
LIMIT {MAX_SEARCH_RESULTS}
""".strip()
    return _execute_sql(statement)


def _search_vendor_contracts(search_text, vendor_id=None, support_tier=None, region=None):
    """Expose retrieval as a bounded tool with redacted tracing and explicit errors.

    Empty queries are non-retryable input errors. Operational failures return a
    stable error shape instead of pretending that no matching evidence exists.
    """
    if not isinstance(search_text, str) or not search_text.strip():
        return json.dumps({
            "ok": False,
            "tool": "search_vendor_contracts",
            "error_code": "CONTRACT_SEARCH_INVALID_REQUEST",
            "message": "A non-empty contract search query is required.",
            "retryable": False,
        })

    with start_retriever_span(
        "contract_search",
        {
            "vendor_filter_present": bool(vendor_id),
            "support_tier_filter_present": bool(support_tier),
            "region_filter_present": bool(region),
            "query_length": len(search_text),
        },
    ) as span:
        try:
            rows = _search_vendor_contract_rows(
                search_text,
                vendor_id=vendor_id,
                support_tier=support_tier,
                region=region,
            )
        except Exception as error:
            message = str(error).lower()
            if "permission" in message or "unauthorized" in message or "forbidden" in message:
                error_code = "CONTRACT_SEARCH_UNAUTHORIZED"
                retryable = False
            elif "invalid" in message or "parameter" in message:
                error_code = "CONTRACT_SEARCH_INVALID_REQUEST"
                retryable = False
            else:
                error_code = "CONTRACT_SEARCH_UNAVAILABLE"
                retryable = True
            logger.exception(
                "Contract search failed: error_code=%s catalog=%s function=%s "
                "warehouse_configured=%s",
                error_code,
                CATALOG,
                CONTRACT_SEARCH_FUNCTION,
                bool(os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")),
            )
            span.set_outputs({"ok": False, "error_code": error_code})
            return json.dumps({
                "ok": False,
                "tool": "search_vendor_contracts",
                "error_code": error_code,
                "message": "Contract search is temporarily unavailable.",
                "retryable": retryable,
            })

        span.set_outputs({"ok": True, "row_count": len(rows)})
        return json.dumps({
            "ok": True,
            "tool": "search_vendor_contracts",
            "row_count": len(rows),
            "rows": rows,
        }, default=str)


search_vendor_contracts = function_tool(
    _search_vendor_contracts,
    name_override="search_vendor_contracts",
)
