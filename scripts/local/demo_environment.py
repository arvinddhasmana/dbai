"""Shared constants and helpers for the disposable Databricks demo."""

from pathlib import Path
import json
import os
import subprocess
import time

from databricks.ai_search.client import AISearchClient
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound


REPOSITORY_ROOT = Path(__file__).parents[2]
CONTRACTS_DIR = REPOSITORY_ROOT / "sample_data" / "vendor_contracts"
CATALOG = os.getenv("DBAI_CATALOG", "globalmart")
SCHEMA = "supply_chain"
VOLUME_NAME = "vendor_contracts"
VOLUME_PATH = os.getenv(
    "VENDOR_CONTRACT_VOLUME",
    f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME_NAME}",
)
SOURCE_TABLE = f"{CATALOG}.{SCHEMA}.vendor_contract_chunks_index_source"
INDEX_NAME = os.getenv(
    "AI_SEARCH_INDEX",
    f"{CATALOG}.{SCHEMA}.vendor_contract_chunks_index_rebuilt",
)
ENDPOINT_NAME = os.getenv("AI_SEARCH_ENDPOINT", "globalmart-supply-chain-search")
ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".html", ".pdf"}


def create_workspace_client():
    return WorkspaceClient()


def create_search_client():
    workspace_url = os.getenv("DATABRICKS_HOST")
    personal_access_token = os.getenv("DATABRICKS_TOKEN")
    if not personal_access_token:
        profile = os.getenv("DATABRICKS_CONFIG_PROFILE")
        command = ["databricks", "auth", "token"]
        if profile:
            command.append(profile)
        command.extend(["-o", "json"])
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            personal_access_token = json.loads(result.stdout)["access_token"]
        except (FileNotFoundError, subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "AI Search requires DATABRICKS_TOKEN or a logged-in Databricks "
                "CLI profile. Run `databricks auth login <profile> --host <workspace-url>` "
                "and set DATABRICKS_CONFIG_PROFILE."
            ) from error
    if not workspace_url:
        workspace_url = WorkspaceClient().config.host
    return AISearchClient(
        workspace_url=workspace_url,
        personal_access_token=personal_access_token,
        disable_notice=True,
    )


def baseline_files():
    return sorted(
        path
        for path in CONTRACTS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS
    )


def upload_file(client, local_file, destination_name=None):
    destination = f"{VOLUME_PATH}/{destination_name or local_file.name}"
    with local_file.open("rb") as contents:
        client.files.upload(destination, contents, overwrite=True)
    print(f"Uploaded {local_file.name} -> {destination}")


def upload_baseline_files(client):
    for local_file in baseline_files():
        upload_file(client, local_file)


def delete_volume_contents(client):
    files = []
    directories = []
    pending = [VOLUME_PATH]
    while pending:
        directory = pending.pop()
        try:
            entries = client.files.list_directory_contents(directory)
        except NotFound:
            print(f"Volume already absent: {VOLUME_PATH}")
            return
        for entry in entries:
            if getattr(entry, "is_directory", False):
                directories.append(entry.path)
                pending.append(entry.path)
            else:
                files.append(entry.path)

    for path in sorted(files, reverse=True):
        client.files.delete(path)
        print(f"Deleted Volume file: {path}")
    for path in sorted(directories, key=lambda value: value.count("/"), reverse=True):
        client.files.delete_directory(path)
        print(f"Deleted Volume directory: {path}")


def execute_sql(client, statement, warehouse_id):
    response = client.statement_execution.execute_statement(
        statement,
        warehouse_id=warehouse_id,
        wait_timeout="30s",
    )

    def statement_state(result):
        state = result.status.state
        return getattr(state, "value", str(state).rsplit(".", 1)[-1])

    while statement_state(response) in {"PENDING", "RUNNING"}:
        time.sleep(2)
        response = client.statement_execution.get_statement(response.statement_id)

    state = statement_state(response)
    if state != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or str(error) or "unknown SQL error"
        raise RuntimeError(f"SQL statement failed ({state}): {message}\n{statement}")
    return response


def execute_sql_file(client, sql_path, warehouse_id):
    statement_lines = []
    sql_text = sql_path.read_text(encoding="utf-8").replace("globalmart", CATALOG)
    for line in sql_text.splitlines():
        if line.strip().startswith("--"):
            continue
        statement_lines.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(statement_lines).strip()[:-1].strip()
            if statement:
                execute_sql(client, statement, warehouse_id)
            statement_lines = []
    trailing_statement = "\n".join(statement_lines).strip()
    if trailing_statement:
        execute_sql(client, trailing_statement, warehouse_id)