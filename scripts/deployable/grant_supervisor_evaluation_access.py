"""Grant and verify least-privilege access for supervisor evaluation."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel
from databricks.sdk.service.sql import WarehouseAccessControlRequest, WarehousePermissionLevel
from databricks.sdk.service.vectorsearch import (
    VectorSearchEndpointAccessControlRequest,
    VectorSearchEndpointPermissionLevel,
)


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
INVENTORY_TABLES = ("dim_products", "dim_vendors", "fact_inventory_status")


def identifier(value: str, label: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value}")
    return f"`{value}`"


def principal(value: str) -> str:
    if not value or any(char in value for char in "\r\n"):
        raise ValueError("Principal must be a non-empty single-line value")
    return f"`{value.replace('`', '``')}`"


def state(response: Any) -> str:
    value = response.status.state
    return getattr(value, "value", str(value).rsplit(".", 1)[-1])


def execute_sql(client: WorkspaceClient, statement: str, warehouse_id: str) -> None:
    response = client.statement_execution.execute_statement(
        statement,
        warehouse_id=warehouse_id,
        wait_timeout="30s",
    )
    while state(response) in {"PENDING", "RUNNING"}:
        time.sleep(2)
        response = client.statement_execution.get_statement(response.statement_id)
    if state(response) != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or str(error) or "unknown SQL error"
        raise RuntimeError(f"SQL failed: {message}\n{statement}")


def query_rows(client: WorkspaceClient, statement: str, warehouse_id: str) -> list[list[Any]]:
    response = client.statement_execution.execute_statement(
        statement,
        warehouse_id=warehouse_id,
        wait_timeout="30s",
    )
    while state(response) in {"PENDING", "RUNNING"}:
        time.sleep(2)
        response = client.statement_execution.get_statement(response.statement_id)
    if state(response) != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or str(error) or "unknown SQL error"
        raise RuntimeError(f"SQL failed: {message}\n{statement}")
    return getattr(getattr(response, "result", None), "data_array", None) or []


def existing_tables(client: WorkspaceClient, catalog: str, schema: str, warehouse_id: str) -> set[str]:
    rows = query_rows(
        client,
        f"SELECT table_name FROM {identifier(catalog, 'catalog')}.information_schema.tables "
        f"WHERE table_schema = '{schema}'",
        warehouse_id,
    )
    return {str(row[0]) for row in rows if row}


def grant_sql_access(
    client: WorkspaceClient,
    catalog: str,
    evaluator: str,
    app_client_id: str,
    evaluation_table: str,
    trace_schema: str,
    warehouse_id: str,
) -> dict[str, list[str]]:
    catalog_sql = identifier(catalog, "catalog")
    principals = {"evaluator": evaluator, "app": app_client_id}
    trace_tables = existing_tables(client, catalog, trace_schema, warehouse_id)
    evaluation_tables = existing_tables(client, catalog, "agent_evaluation", warehouse_id)
    supply_tables = existing_tables(client, catalog, "supply_chain", warehouse_id)
    statements: dict[str, list[str]] = {key: [] for key in principals}

    for label, name in principals.items():
        target = principal(name)
        statements[label].append(f"GRANT USE CATALOG ON CATALOG {catalog_sql} TO {target}")
        statements[label].append(
            f"GRANT USE SCHEMA ON SCHEMA {catalog_sql}.`supply_chain` TO {target}"
        )
        for table in INVENTORY_TABLES:
            if table in supply_tables:
                statements[label].append(
                    f"GRANT SELECT ON TABLE {catalog_sql}.`supply_chain`.`{table}` TO {target}"
                )

    evaluator_sql = principal(evaluator)
    statements["evaluator"].append(
        f"GRANT USE SCHEMA ON SCHEMA {catalog_sql}.`agent_evaluation` TO {evaluator_sql}"
    )
    if evaluation_table in evaluation_tables:
        statements["evaluator"].append(
            f"GRANT SELECT ON TABLE {catalog_sql}.`agent_evaluation`.`{evaluation_table}` TO {evaluator_sql}"
        )
    else:
        raise RuntimeError(f"Evaluation dataset table not found: {catalog}.agent_evaluation.{evaluation_table}")

    for label, name in principals.items():
        target = principal(name)
        statements[label].append(
            f"GRANT USE SCHEMA ON SCHEMA {catalog_sql}.`{trace_schema}` TO {target}"
        )
        for table in sorted(table for table in trace_tables if table.startswith("supervisor_traces")):
            statements[label].append(
                f"GRANT SELECT ON TABLE {catalog_sql}.`{trace_schema}`.`{table}` TO {target}"
            )
    for statement in statements["evaluator"] + statements["app"]:
        execute_sql(client, statement, warehouse_id)
    return statements


def grant_workspace_access(
    client: WorkspaceClient,
    evaluator: str,
    app_name: str,
    app_client_id: str,
    app_service_principal_name: str | None,
    warehouse_id: str,
    genie_space_id: str,
    model_endpoint: str,
    vector_search_endpoint: str,
    experiment_id: str,
) -> None:
    client.warehouses.update_permissions(
        warehouse_id,
        access_control_list=[
            WarehouseAccessControlRequest(
                user_name=evaluator,
                permission_level=WarehousePermissionLevel.CAN_USE,
            )
        ],
    )
    client.permissions.update(
        "apps",
        app_name,
        access_control_list=[
            AccessControlRequest(user_name=evaluator, permission_level=PermissionLevel.CAN_USE)
        ],
    )
    client.permissions.update(
        "experiments",
        experiment_id,
        access_control_list=[
            AccessControlRequest(user_name=evaluator, permission_level=PermissionLevel.CAN_MANAGE)
        ],
    )
    tool_principals = [
        AccessControlRequest(user_name=evaluator, permission_level=PermissionLevel.CAN_RUN),
        AccessControlRequest(
            service_principal_name=app_client_id,
            permission_level=PermissionLevel.CAN_RUN,
        ),
    ]
    client.permissions.update("genie", genie_space_id, access_control_list=tool_principals)

    endpoint = client.serving_endpoints.get(model_endpoint)
    if endpoint.id:
        client.permissions.update(
            "serving-endpoints",
            endpoint.id,
            access_control_list=[
                AccessControlRequest(user_name=evaluator, permission_level=PermissionLevel.CAN_QUERY),
                AccessControlRequest(
                    service_principal_name=app_client_id,
                    permission_level=PermissionLevel.CAN_QUERY,
                ),
            ],
        )
    else:
        print(
            f"Model endpoint {model_endpoint} has no permissions ID; "
            "it is a foundation model API endpoint with workspace-governed access."
        )

    search_endpoint = client.vector_search_endpoints.get_endpoint(vector_search_endpoint)
    endpoint_acl = [
        VectorSearchEndpointAccessControlRequest(
            user_name=evaluator,
            permission_level=VectorSearchEndpointPermissionLevel.CAN_USE,
        )
    ]
    if app_client_id:
        endpoint_acl.append(
            VectorSearchEndpointAccessControlRequest(
                service_principal_name=app_client_id,
                permission_level=VectorSearchEndpointPermissionLevel.CAN_USE,
            )
        )
    client.vector_search_endpoints.update_permissions(
        search_endpoint.id,
        access_control_list=endpoint_acl,
    )


def grant_access(
    profile: str | None,
    app_name: str,
    catalog: str,
    warehouse_id: str,
    genie_space_id: str,
    model_endpoint: str,
    vector_search_endpoint: str,
    experiment_id: str,
    evaluation_table: str,
    trace_schema: str,
) -> dict[str, Any]:
    client = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    evaluator = client.current_user.me().user_name
    app = client.apps.get(app_name)
    app_client_id = app.service_principal_client_id
    app_service_principal_name = app.service_principal_name
    if not app_client_id:
        raise RuntimeError(f"App has no service principal client ID: {app_name}")

    statements = grant_sql_access(
        client,
        catalog,
        evaluator,
        app_client_id,
        evaluation_table,
        trace_schema,
        warehouse_id,
    )
    grant_workspace_access(
        client,
        evaluator,
        app_name,
        app_client_id,
        app_service_principal_name,
        warehouse_id,
        genie_space_id,
        model_endpoint,
        vector_search_endpoint,
        experiment_id,
    )
    result = {
        "evaluator": evaluator,
        "app_name": app_name,
        "app_service_principal_client_id": app_client_id,
        "app_service_principal_name": app_service_principal_name,
        "warehouse_id": warehouse_id,
        "experiment_id": experiment_id,
        "sql_grants": statements,
    }
    print(json.dumps(result, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.getenv("DATABRICKS_CONFIG_PROFILE"))
    parser.add_argument("--app-name", default="agent-supply-chain-sup-dev")
    parser.add_argument("--catalog", default=os.getenv("DBAI_CATALOG", "globalmart"))
    parser.add_argument("--warehouse-id", default=os.getenv("DATABRICKS_SQL_WAREHOUSE_ID", "a749a7ee30b8f4f4"))
    parser.add_argument("--genie-space-id", default="01f1ab3249ea18269d5edc4f599b895c")
    parser.add_argument("--model-endpoint", default="databricks-meta-llama-3-3-70b-instruct")
    parser.add_argument("--vector-search-endpoint", default="globalmart-supply-chain-search")
    parser.add_argument("--experiment-id", default="2285133248665247")
    parser.add_argument("--evaluation-table", default="supervisor_cases")
    parser.add_argument("--trace-schema", default="agent_observability")
    args = parser.parse_args()
    grant_access(
        profile=args.profile,
        app_name=args.app_name,
        catalog=args.catalog,
        warehouse_id=args.warehouse_id,
        genie_space_id=args.genie_space_id,
        model_endpoint=args.model_endpoint,
        vector_search_endpoint=args.vector_search_endpoint,
        experiment_id=args.experiment_id,
        evaluation_table=args.evaluation_table,
        trace_schema=args.trace_schema,
    )


if __name__ == "__main__":
    main()