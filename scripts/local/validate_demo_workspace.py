"""Validate live Databricks prerequisites for the GlobalMart demo."""

import argparse
import os

from databricks.sdk import WorkspaceClient

from create_vendor_contract_index import EMBEDDING_ENDPOINT
from demo_environment import SOURCE_TABLE, create_workspace_client, execute_sql


REQUIRED_COLUMNS = {
    "chunk_id",
    "file_id",
    "content_hash",
    "document_version",
    "is_active",
    "processed_at",
    "source_path",
    "source_file",
    "source_modified_at",
    "source_size_bytes",
    "vendor_id",
    "vendor_name",
    "support_tier",
    "region_covered",
    "chunk_index",
    "chunk_text",
    "token_count",
}


def _enum_text(value):
    return str(getattr(value, "value", value) or "").upper()


def validate_source_table(table_info):
    errors = []
    table_type = _enum_text(getattr(table_info, "table_type", None))
    data_format = _enum_text(getattr(table_info, "data_source_format", None))

    if table_type not in {"MANAGED", "EXTERNAL"}:
        errors.append(
            f"{SOURCE_TABLE} must be a regular MANAGED or EXTERNAL table; "
            f"found {table_type or 'unknown'}.",
        )
    if data_format != "DELTA":
        errors.append(f"{SOURCE_TABLE} must use Delta format; found {data_format or 'unknown'}.")

    columns = {
        str(getattr(column, "name", "")).lower()
        for column in (getattr(table_info, "columns", None) or [])
    }
    missing_columns = sorted(REQUIRED_COLUMNS - columns)
    if missing_columns:
        errors.append(f"{SOURCE_TABLE} is missing columns: {', '.join(missing_columns)}.")

    properties = {
        str(key).lower(): str(value).lower()
        for key, value in (getattr(table_info, "properties", None) or {}).items()
    }
    if properties.get("delta.enablechangedatafeed") != "true":
        errors.append(f"{SOURCE_TABLE} must have delta.enableChangeDataFeed=true.")
    return errors


def _validate_serving_endpoint(client, endpoint_name, purpose):
    endpoint = client.serving_endpoints.get(endpoint_name)
    ready = _enum_text(getattr(getattr(endpoint, "state", None), "ready", None))
    if ready != "READY":
        raise RuntimeError(
            f"{purpose} endpoint {endpoint_name!r} is not ready (state={ready or 'unknown'}).",
        )
    print(f"PASS {purpose} endpoint: {endpoint_name}")


def validate_workspace(warehouse_id, model_endpoint=None, require_index=False):
    if not warehouse_id:
        raise RuntimeError("Set DATABRICKS_SQL_WAREHOUSE_ID or pass --warehouse-id.")

    model_endpoint = model_endpoint or os.getenv(
        "MODEL_ENDPOINT", "databricks-llama-4-maverick"
    )
    workspace = create_workspace_client()
    warehouse = workspace.warehouses.get(warehouse_id)
    print(
        f"PASS SQL warehouse: {getattr(warehouse, 'name', warehouse_id)} "
        f"({_enum_text(getattr(warehouse, 'state', None)) or 'state unknown'})",
    )

    table_info = workspace.tables.get(SOURCE_TABLE)
    source_errors = validate_source_table(table_info)
    if source_errors:
        raise RuntimeError("Source table validation failed:\n- " + "\n- ".join(source_errors))
    print(f"PASS source table: {SOURCE_TABLE} is a regular Delta table with CDF enabled")

    execute_sql(
        workspace,
        f"SELECT chunk_id, chunk_text FROM {SOURCE_TABLE} LIMIT 1",
        warehouse_id,
    )
    print("PASS SQL access: source table can be queried with the current identity")

    _validate_serving_endpoint(workspace, EMBEDDING_ENDPOINT, "Embedding")
    _validate_serving_endpoint(workspace, model_endpoint, "Model")

    if require_index:
        from databricks.ai_search.exceptions import NotFound

        from demo_environment import ENDPOINT_NAME, INDEX_NAME, create_search_client

        search_client = create_search_client()
        try:
            search_client.get_index(ENDPOINT_NAME, INDEX_NAME)
        except NotFound as error:
            raise RuntimeError(
                f"AI Search index {INDEX_NAME} was not found on {ENDPOINT_NAME}.",
            ) from error
        print(f"PASS AI Search index: {INDEX_NAME}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warehouse-id",
        default=os.getenv("DATABRICKS_SQL_WAREHOUSE_ID"),
        help="SQL warehouse ID; defaults to DATABRICKS_SQL_WAREHOUSE_ID.",
    )
    parser.add_argument(
        "--model-endpoint",
        default=os.getenv("MODEL_ENDPOINT", "databricks-llama-4-maverick"),
        help="Chat model endpoint; defaults to MODEL_ENDPOINT.",
    )
    parser.add_argument(
        "--require-index",
        action="store_true",
        help="Also verify that the configured AI Search index exists.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    validate_workspace(args.warehouse_id, args.model_endpoint, args.require_index)
    print("Workspace preflight passed.")


if __name__ == "__main__":
    main()