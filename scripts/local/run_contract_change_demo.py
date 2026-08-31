"""Run the add, update, and delete contract lifecycle demo."""

import argparse
import os
import subprocess

from demo_environment import (
    CONTRACTS_DIR,
    REPOSITORY_ROOT,
    VOLUME_PATH,
    baseline_files,
    create_workspace_client,
    upload_file,
)

NEW_CONTRACT = CONTRACTS_DIR / "demo" / "add" / "Contract_VEND321_Platinum.txt"
UPDATED_CONTRACT = CONTRACTS_DIR / "demo" / "update" / "Contract_VEND456_Silver.txt"
DELETED_CONTRACT_NAME = "Contract_VEND123_Bronze.txt"
DEMO_NEW_CONTRACT_NAME = NEW_CONTRACT.name


def delete_file(client, file_name):
    destination = f"{VOLUME_PATH}/{file_name}"
    try:
        client.files.delete(destination)
        print(f"Deleted {destination}")
    except Exception as error:
        if "404" not in str(error) and "not found" not in str(error).lower():
            raise
        print(f"Already absent: {destination}")


def run_refresh(target, mode):
    command = [
        "databricks", "bundle", "run", "refresh_vendor_contract_chunks",
        "-t", target, "--notebook-params", f"INGESTION_MODE={mode}",
    ]
    print(f"Running {' '.join(command)}")
    subprocess.run(command, check=True, cwd=REPOSITORY_ROOT, env=os.environ.copy())


def run_vendor_upsert(target):
    command = ["databricks", "bundle", "run", "upsert_demo_vendor", "-t", target]
    print(f"Running {' '.join(command)}")
    subprocess.run(command, check=True, cwd=REPOSITORY_ROOT, env=os.environ.copy())


def apply_action(action, client):
    if action == "reset":
        for local_file in baseline_files():
            upload_file(client, local_file)
        delete_file(client, DEMO_NEW_CONTRACT_NAME)
        return "full_rebuild"
    if action == "add":
        upload_file(client, NEW_CONTRACT)
    elif action == "update":
        upload_file(client, UPDATED_CONTRACT, "Contract_VEND456_Silver.txt")
    elif action == "delete":
        delete_file(client, DELETED_CONTRACT_NAME)
    return "incremental"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("reset", "add", "update", "delete"),
        help="Lifecycle state to apply to the contract Volume.",
    )
    parser.add_argument("--target", default="dev", help="Databricks bundle target.")
    parser.add_argument(
        "--no-run", action="store_true",
        help="Apply the Volume change without starting the refresh job.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    client = create_workspace_client()
    refresh_mode = apply_action(args.action, client)
    if not args.no_run:
        run_refresh(args.target, refresh_mode)
        if args.action == "add":
            run_vendor_upsert(args.target)
    else:
        print("Refresh skipped (--no-run).")


if __name__ == "__main__":
    main()
