"""Destroy the disposable GlobalMart Databricks demo environment."""

import argparse
import os
from pathlib import Path
import subprocess
import time


environment_name = os.getenv("DBAI_ENVIRONMENT", "demo")
catalog_state_file = Path(
    os.getenv("DBAI_STATE_DIR", ".dbai-state")
) / f"{environment_name}-catalog"
if not os.getenv("DBAI_CATALOG") and catalog_state_file.is_file():
    os.environ["DBAI_CATALOG"] = catalog_state_file.read_text(encoding="utf-8").strip()

from databricks.ai_search.exceptions import NotFound as SearchNotFound
from databricks.sdk.errors import NotFound as DatabricksNotFound

from demo_environment import (
    CATALOG,
    ENDPOINT_NAME,
    INDEX_NAME,
    SCHEMA,
    SOURCE_TABLE,
    VOLUME_NAME,
    VOLUME_PATH,
    create_search_client,
    create_workspace_client,
    delete_volume_contents,
)


REPOSITORY_ROOT = Path(__file__).parents[2]
TABLES = (
    f"{CATALOG}.{SCHEMA}.contract_file_events_bronze",
    f"{CATALOG}.{SCHEMA}.contract_file_manifest",
    f"{CATALOG}.{SCHEMA}.contract_documents_silver",
    SOURCE_TABLE,
    f"{CATALOG}.{SCHEMA}.dim_products",
    f"{CATALOG}.{SCHEMA}.dim_vendors",
    f"{CATALOG}.{SCHEMA}.fact_inventory_status",
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default="dev", help="Databricks bundle target.")
    parser.add_argument(
        "--yes", action="store_true", help="Confirm destructive deletion without a prompt."
    )
    parser.add_argument(
        "--keep-bundle-resources",
        action="store_true",
        help="Keep bundle-managed jobs and the custom supply-chain app.",
    )
    parser.add_argument(
        "--drop-schema",
        action="store_true",
        help="Drop the supply_chain schema after deleting its named demo objects.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the deletion plan without contacting Databricks."
    )
    return parser.parse_args()


def delete_search_objects():
    client = create_search_client()
    try:
        client.get_index(ENDPOINT_NAME, INDEX_NAME)
    except SearchNotFound:
        print(f"Search index already absent: {INDEX_NAME}")
    else:
        client.delete_index(endpoint_name=ENDPOINT_NAME, index_name=INDEX_NAME)
        print(f"Deleted AI Search index: {INDEX_NAME}")
        for _ in range(30):
            if not client.index_exists(endpoint_name=ENDPOINT_NAME, index_name=INDEX_NAME):
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"Timed out waiting for Search index deletion: {INDEX_NAME}")

    if client.endpoint_exists(ENDPOINT_NAME):
        client.delete_endpoint(ENDPOINT_NAME)
        print(f"Deleted AI Search endpoint: {ENDPOINT_NAME}")
    else:
        print(f"AI Search endpoint already absent: {ENDPOINT_NAME}")


def destroy_bundle(target):
    command = ["databricks", "bundle", "destroy", "-t", target, "--auto-approve"]
    print(f"Running {' '.join(command)}")
    subprocess.run(command, check=True, cwd=REPOSITORY_ROOT, env=os.environ.copy())


def delete_catalog_object(delete_operation, object_name, label):
    try:
        delete_operation(object_name)
        print(f"Deleted {label}: {object_name}")
    except DatabricksNotFound:
        print(f"Already absent {label}: {object_name}")
    except Exception as error:
        print(f"Skipped {label} {object_name}: {error}")
        return False
    return True


def destroy_catalog_objects(workspace, drop_schema):
    succeeded = True
    succeeded &= delete_catalog_object(
        lambda name: workspace.functions.delete(name, force=True),
        f"{CATALOG}.{SCHEMA}.search_vendor_contracts",
        "function",
    )
    for table in TABLES:
        succeeded &= delete_catalog_object(workspace.tables.delete, table, "table")
    succeeded &= delete_catalog_object(
        workspace.volumes.delete,
        f"{CATALOG}.{SCHEMA}.{VOLUME_NAME}",
        "Volume",
    )
    if drop_schema:
        try:
            workspace.schemas.delete(f"{CATALOG}.{SCHEMA}", force=True)
            print(f"Deleted schema: {CATALOG}.{SCHEMA}")
        except DatabricksNotFound:
            print(f"Already absent schema: {CATALOG}.{SCHEMA}")
        except Exception as error:
            succeeded = False
            print(f"Skipped schema {CATALOG}.{SCHEMA}: {error}")
    return succeeded


def main():
    args = parse_args()
    plan = [
        f"AI Search index {INDEX_NAME}",
        f"AI Search endpoint {ENDPOINT_NAME}",
        f"all files and directories under {VOLUME_PATH}",
        f"Volume {CATALOG}.{SCHEMA}.{VOLUME_NAME}",
        "the Genie function search_vendor_contracts",
        *TABLES,
    ]
    if not args.keep_bundle_resources:
        plan.append(f"bundle jobs for target {args.target}")
        plan.append(f"custom supply-chain app for target {args.target}")
    if args.drop_schema:
        plan.append(f"schema {CATALOG}.{SCHEMA}")
    print("Destructive teardown plan:")
    for item in plan:
        print(f"- {item}")
    if args.dry_run:
        return
    if not args.yes:
        raise SystemExit("Pass --yes to confirm destructive teardown.")

    delete_search_objects()
    workspace = create_workspace_client()
    delete_volume_contents(workspace)
    catalog_cleanup_succeeded = destroy_catalog_objects(workspace, args.drop_schema)
    if not catalog_cleanup_succeeded:
        raise SystemExit(
            "Some Unity Catalog objects remain; review the skipped-operation errors above. "
            "Azure deletion was not attempted."
        )
    if not args.keep_bundle_resources:
        destroy_bundle(args.target)
    print(f"Demo teardown complete. The {CATALOG} catalog was retained.")


if __name__ == "__main__":
    main()