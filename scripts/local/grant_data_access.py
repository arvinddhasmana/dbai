"""Grant the App and signed-in user least-privilege access to demo data."""

import argparse
import os
import re
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.vectorsearch import (
    VectorSearchEndpointAccessControlRequest,
    VectorSearchEndpointPermissionLevel,
)


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TABLES = (
    "dim_products",
    "dim_vendors",
    "fact_inventory_status",
    "vendor_contract_chunks_index_rebuilt",
)
BOOTSTRAP_TABLES = (
    "dim_products",
    "dim_vendors",
    "fact_inventory_status",
    "contract_file_events_bronze",
    "contract_file_manifest",
    "contract_documents_silver",
    "vendor_contract_chunks_index_source",
)


def sql_identifier(value, label):
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise SystemExit(f"Invalid {label}: {value}")
    return f"`{value}`"


def sql_principal(value):
    if not value or any(character in value for character in "\r\n"):
        raise SystemExit("A non-empty single-line principal is required.")
    return f"`{value.replace('`', '``')}`"


def execute_sql(client, statement, warehouse_id):
    response = client.statement_execution.execute_statement(
        statement,
        warehouse_id=warehouse_id,
        wait_timeout="30s",
    )

    def state(result):
        value = result.status.state
        return getattr(value, "value", str(value).rsplit(".", 1)[-1])

    while state(response) in {"PENDING", "RUNNING"}:
        time.sleep(2)
        response = client.statement_execution.get_statement(response.statement_id)

    if state(response) != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or str(error) or "unknown SQL error"
        raise RuntimeError(f"Databricks SQL failed: {message}\n{statement}")


def query_rows(client, statement, warehouse_id):
    response = client.statement_execution.execute_statement(
        statement,
        warehouse_id=warehouse_id,
        wait_timeout="30s",
    )
    while True:
        value = response.status.state
        state = getattr(value, "value", str(value).rsplit(".", 1)[-1])
        if state not in {"PENDING", "RUNNING"}:
            break
        time.sleep(2)
        response = client.statement_execution.get_statement(response.statement_id)
    if state != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or str(error) or "unknown SQL error"
        raise RuntimeError(f"Databricks SQL failed: {message}\n{statement}")
    return getattr(getattr(response, "result", None), "data_array", None) or []


def grant_bootstrap_modify(client, catalog, principals, warehouse_id):
    rows = query_rows(
        client,
        f"SELECT table_name FROM {catalog}.information_schema.tables "
        "WHERE table_schema = 'supply_chain' "
        "AND table_name IN ("
        "'dim_products', 'dim_vendors', 'fact_inventory_status', "
        "'contract_file_events_bronze', 'contract_file_manifest', "
        "'contract_documents_silver', 'vendor_contract_chunks_index_source')",
        warehouse_id,
    )
    existing_tables = {row[0] for row in rows}
    for principal in principals:
        principal_sql = sql_principal(principal)
        for table in BOOTSTRAP_TABLES:
            if table not in existing_tables:
                continue
            statement = (
                f"GRANT SELECT, MODIFY ON TABLE {catalog}.`supply_chain`.`{table}` "
                f"TO {principal_sql}"
            )
            execute_sql(client, statement, warehouse_id)
            print(f"Granted bootstrap write access: {statement}")


def grant_sql_access(client, catalog, principal, warehouse_id):
    principal_sql = sql_principal(principal)
    statements = [
        f"GRANT USE CATALOG ON CATALOG {catalog} TO {principal_sql}",
        f"GRANT USE SCHEMA ON SCHEMA {catalog}.`supply_chain` TO {principal_sql}",
    ]
    statements.extend(
        f"GRANT SELECT ON TABLE {catalog}.`supply_chain`.`{table}` TO {principal_sql}"
        for table in TABLES
    )
    statements.append(
        f"GRANT EXECUTE ON FUNCTION {catalog}.`supply_chain`.`search_vendor_contracts` "
        f"TO {principal_sql}"
    )
    for statement in statements:
        execute_sql(client, statement, warehouse_id)
        print(f"Granted data access: {statement}")


def grant_data_access(
    app_name,
    user_principal,
    catalog_name,
    warehouse_id,
    ai_search_endpoint,
    bootstrap_principals=(),
    prepare_only=False,
    client=None,
):
    catalog = sql_identifier(catalog_name, "catalog")
    client = client or WorkspaceClient()
    if prepare_only:
        if not bootstrap_principals:
            raise SystemExit("Pass at least one --bootstrap-principal.")
        grant_bootstrap_modify(client, catalog, bootstrap_principals, warehouse_id)
        return

    app_principal = None
    if app_name:
        app = client.apps.get(app_name)
        app_principal = app.service_principal_client_id
        if not app_principal:
            raise SystemExit(f"App has no service principal client ID: {app_name}")

    principals = []
    for principal in (app_principal, user_principal):
        if principal and principal not in principals:
            principals.append(principal)
    if not principals:
        raise SystemExit("Pass --app-name and/or --user-principal.")

    if bootstrap_principals:
        grant_bootstrap_modify(client, catalog, bootstrap_principals, warehouse_id)

    for principal in principals:
        grant_sql_access(client, catalog, principal, warehouse_id)

    if app_principal:
        try:
            endpoint = client.vector_search_endpoints.get_endpoint(ai_search_endpoint)
        except NotFound:
            print(
                "AI Search endpoint not found; skipped App endpoint access: "
                f"{ai_search_endpoint}"
            )
        else:
            client.vector_search_endpoints.update_permissions(
                endpoint.id,
                access_control_list=[
                    VectorSearchEndpointAccessControlRequest(
                        service_principal_name=app_principal,
                        permission_level=VectorSearchEndpointPermissionLevel.CAN_USE,
                    )
                ],
            )
            print(f"Granted App CAN_USE on AI Search endpoint: {ai_search_endpoint}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", help="Deployed Databricks App name.")
    parser.add_argument(
        "--bootstrap-principal",
        action="append",
        default=[],
        help="Identity that runs overwrite-based bootstrap jobs; may be repeated.",
    )
    parser.add_argument(
        "--prepare-bootstrap",
        action="store_true",
        help="Grant MODIFY on existing Gold tables and skip App/user grants.",
    )
    parser.add_argument(
        "--user-principal",
        default=os.getenv("DBAI_APP_USER"),
        help="Databricks username used by App on-behalf-of requests.",
    )
    parser.add_argument("--catalog", default=os.getenv("DBAI_CATALOG"))
    parser.add_argument("--warehouse-id", default=os.getenv("DATABRICKS_SQL_WAREHOUSE_ID"))
    parser.add_argument(
        "--ai-search-endpoint",
        default=os.getenv("AI_SEARCH_ENDPOINT", "globalmart-supply-chain-search"),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.catalog:
        raise SystemExit("Set DBAI_CATALOG or pass --catalog.")
    if not args.warehouse_id:
        raise SystemExit("Set DATABRICKS_SQL_WAREHOUSE_ID or pass --warehouse-id.")
    grant_data_access(
        args.app_name,
        args.user_principal,
        args.catalog,
        args.warehouse_id,
        args.ai_search_endpoint,
        args.bootstrap_principal,
        args.prepare_bootstrap,
    )


if __name__ == "__main__":
    main()