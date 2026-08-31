"""Create and populate the disposable GlobalMart Databricks demo."""

import argparse
import os
from pathlib import Path
import subprocess

from databricks.sdk.errors import NotFound

from create_vendor_contract_index import provision_index
from demo_environment import (
    CATALOG,
    ENDPOINT_NAME,
    INDEX_NAME,
    SCHEMA,
    VOLUME_NAME,
    create_search_client,
    create_workspace_client,
    execute_sql,
    execute_sql_file,
    upload_baseline_files,
)
from grant_data_access import grant_bootstrap_modify, grant_data_access
from validate_demo_workspace import validate_workspace


REPOSITORY_ROOT = Path(__file__).parents[2]
GENIE_SQL = REPOSITORY_ROOT / "sql" / "01_genie_search.sql"


def run_command(command, target):
    full_command = ["databricks", "bundle", *command, "-t", target]
    print(f"Running {' '.join(full_command)}")
    subprocess.run(full_command, check=True, cwd=REPOSITORY_ROOT, env=os.environ.copy())


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="dev", help="Databricks bundle target.")
    parser.add_argument(
        "--warehouse-id",
        default=os.getenv("DATABRICKS_SQL_WAREHOUSE_ID"),
        help="SQL warehouse ID; defaults to DATABRICKS_SQL_WAREHOUSE_ID.",
    )
    parser.add_argument(
        "--skip-deploy", action="store_true", help="Do not deploy the bundle before running jobs."
    )
    parser.add_argument(
        "--skip-index", action="store_true", help="Create tables and function without AI Search objects."
    )
    parser.add_argument(
        "--skip-genie", action="store_true", help="Do not create the Genie-facing SQL function."
    )
    parser.add_argument(
        "--app-name",
        default=os.getenv("DBAI_APP_NAME"),
        help="Deployed Databricks App to grant access after bootstrap.",
    )
    parser.add_argument(
        "--user-principal",
        default=os.getenv("DBAI_APP_USER"),
        help="Databricks user to grant access for App on-behalf-of requests.",
    )
    parser.add_argument(
        "--bootstrap-principal",
        action="append",
        default=[],
        help="Identity that runs overwrite-based bootstrap jobs; may be repeated.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.warehouse_id:
        raise SystemExit("Set DATABRICKS_SQL_WAREHOUSE_ID or pass --warehouse-id.")

    workspace = create_workspace_client()
    try:
        workspace.catalogs.get(CATALOG)
    except NotFound:
        workspace.catalogs.create(name=CATALOG)
    execute_sql(workspace, f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}", args.warehouse_id)
    execute_sql(
        workspace,
        f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME_NAME}",
        args.warehouse_id,
    )

    current_user = workspace.current_user.me()
    current_principal = getattr(current_user, "user_name", None)
    if not current_principal:
        current_principal = getattr(current_user, "userName", None)
    bootstrap_principals = list(args.bootstrap_principal)
    if args.user_principal and not bootstrap_principals:
        bootstrap_principals.append(args.user_principal)
    if current_principal and current_principal not in bootstrap_principals:
        bootstrap_principals.append(current_principal)
    if bootstrap_principals:
        grant_bootstrap_modify(
            workspace,
            CATALOG,
            bootstrap_principals,
            args.warehouse_id,
        )

    if not args.skip_deploy:
        run_command(["deploy"], args.target)
    run_command(["run", "generate_mock_data"], args.target)
    upload_baseline_files(workspace)
    run_command(
        ["run", "refresh_vendor_contract_chunks", "--notebook-params", "INGESTION_MODE=full_rebuild"],
        args.target,
    )

    if not args.skip_index:
        validate_workspace(args.warehouse_id)
        provision_index(create_search_client())
        print(f"AI Search objects ready: {ENDPOINT_NAME} / {INDEX_NAME}")
    if not args.skip_genie:
        execute_sql_file(workspace, GENIE_SQL, args.warehouse_id)
        print("Genie function and table metadata created.")

    if args.app_name or args.user_principal:
        if not args.app_name or not args.user_principal:
            raise SystemExit(
                "Pass both --app-name and --user-principal to configure App access."
            )
        grant_data_access(
            args.app_name,
            args.user_principal,
            CATALOG,
            args.warehouse_id,
            ENDPOINT_NAME,
            client=workspace,
        )

    print("Bootstrap complete. The TRIGGERED AI Search index still requires a manual sync.")


if __name__ == "__main__":
    main()