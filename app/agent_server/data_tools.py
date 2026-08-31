"""Databricks tools used by the GlobalMart supply-chain agent."""

import json
import os
import time

from databricks.sdk import WorkspaceClient
from agents import function_tool

from agent_server.product_matching import normalize_product_name
from agent_server.sql_guard import validate_read_only_sql
from agent_server.utils import get_user_workspace_client


CATALOG = os.getenv("DBAI_CATALOG", "globalmart")
CONTRACT_SEARCH_FUNCTION = f"{CATALOG}.supply_chain.search_vendor_contracts"
MAX_SEARCH_RESULTS = 10


def _warehouse_id():
    warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
    if not warehouse_id:
        raise RuntimeError(
            "DATABRICKS_SQL_WAREHOUSE_ID is not configured for this app."
        )
    return warehouse_id


def _sql_literal(value):
    if value is None or not str(value).strip():
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _execute_sql(statement):
    client = get_user_workspace_client()
    response = client.statement_execution.execute_statement(
        statement,
        warehouse_id=_warehouse_id(),
        wait_timeout="30s",
    )
    while str(response.status.state) in {"PENDING", "RUNNING"}:
        time.sleep(1)
        response = client.statement_execution.get_statement(response.statement_id)

    state = str(response.status.state).rsplit(".", 1)[-1]
    if state != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or str(error) or "unknown SQL error"
        raise RuntimeError(f"Databricks SQL failed ({state}): {message}")

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


def _json_result(tool_name, rows):
    return json.dumps(
        {"tool": tool_name, "row_count": len(rows), "rows": rows},
        default=str,
    )


def _lookup_inventory(
    product_name=None,
    vendor_id=None,
    account_manager=None,
    delayed_only=False,
):
    """Return authoritative inventory facts using fixed, governed joins.

    Product-name matching is case-insensitive and accepts a singular final
    word, so "coat" and "coats" resolve to the same product. The tool returns
    all matching rows plus the delayed subset so a false delayed premise can be
    corrected from the data instead of being guessed by the model.
    """
    filters = []
    normalized_product = normalize_product_name(product_name)
    if normalized_product:
        filters.append(
            "lower(p.product_name) LIKE "
            + _sql_literal(f"%{normalized_product}%")
        )
    if vendor_id and str(vendor_id).strip():
        filters.append(f"upper(v.vendor_id) = upper({_sql_literal(vendor_id)})")
    if account_manager and str(account_manager).strip():
        filters.append(
            f"lower(v.account_manager) = lower({_sql_literal(account_manager)})"
        )
    where_clause = "\n  AND ".join(filters) or "TRUE"
    statement = f"""
SELECT
  p.product_name,
  p.SKU AS sku,
  v.vendor_id,
  v.vendor_name,
  v.account_manager,
  f.log_date,
  f.warehouse_location,
  f.stock_on_hand,
  f.units_in_transit,
  f.transit_status,
  f.stock_on_hand * p.unit_cost_usd AS inventory_value_usd
FROM {CATALOG}.supply_chain.fact_inventory_status f
JOIN {CATALOG}.supply_chain.dim_products p ON f.product_id = p.product_id
JOIN {CATALOG}.supply_chain.dim_vendors v ON f.vendor_id = v.vendor_id
WHERE {where_clause}
ORDER BY f.log_date DESC, p.product_name
LIMIT 100
""".strip()
    rows = _execute_sql(statement)
    delayed_rows = [
        row for row in rows if row.get("transit_status") == "Delayed in Transit"
    ]
    if delayed_only:
        return json.dumps(
            {
                "tool": "lookup_inventory",
                "row_count": len(delayed_rows),
                "matching_rows": rows,
                "delayed_rows": delayed_rows,
            },
            default=str,
        )
    return json.dumps(
        {"tool": "lookup_inventory", "row_count": len(rows), "rows": rows},
        default=str,
    )


lookup_inventory = function_tool(_lookup_inventory)


@function_tool
def query_inventory(sql):
    """Run a read-only SQL query over the approved supply-chain Gold tables.

    Use this for exact counts, totals, dates, inventory values, product facts,
    vendor facts, and joins between dim_products, dim_vendors, and
    fact_inventory_status. Provide one SELECT or WITH query using fully
    qualified table names.
    """
    safe_sql = validate_read_only_sql(sql)
    return _json_result("query_inventory", _execute_sql(safe_sql))


def _search_vendor_contracts(
    search_text,
    vendor_id=None,
    support_tier=None,
    region=None,
):
    """Retrieve current active contract chunks with source citations.

    Use this for contract language such as penalties, weather exceptions,
    service levels, liability, delivery obligations, and vendor terms. The
    result includes source_file, chunk_index, vendor metadata, score, and
    chunk_text. Never use deleted lifecycle records as contract evidence.
    """
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
    return _json_result("search_vendor_contracts", _execute_sql(statement))


search_vendor_contracts = function_tool(_search_vendor_contracts)
