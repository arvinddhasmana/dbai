#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPOSITORY_ROOT"

if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' 'The jq command is required to locate the Bundle-uploaded App source.' >&2
  exit 1
fi

target="${DBAI_BUNDLE_TARGET:-${DBAI_ENVIRONMENT:-dev}}"
catalog_name="${DBAI_CATALOG:?Set DBAI_CATALOG to the existing Unity Catalog catalog.}"
warehouse_id="${DATABRICKS_SQL_WAREHOUSE_ID:?Set DATABRICKS_SQL_WAREHOUSE_ID to the existing SQL Warehouse ID.}"
app_name="${DBAI_APP_NAME:-dbai-${target}-supply-chain-agent}"

ensure_app_running() {
  local app_json compute_state attempt

  if ! app_json="$(databricks apps get "$app_name" --output json 2>/dev/null)"; then
    printf 'Databricks App does not exist. Deploy the workload Bundle first: %s\n' "$app_name" >&2
    exit 1
  fi

  compute_state="$(jq -r '.compute_status.state // empty' <<< "$app_json")"
  if [[ "$compute_state" == "STOPPED" ]]; then
    printf 'Starting Databricks App after Bootstrap completed: %s\n' "$app_name"
    databricks apps start "$app_name" --no-wait
  fi

  for ((attempt = 1; attempt <= 120; attempt++)); do
    if ! app_json="$(databricks apps get "$app_name" --output json 2>/dev/null)"; then
      printf 'Could not read Databricks App state while starting %s.\n' "$app_name" >&2
      exit 1
    fi
    compute_state="$(jq -r '.compute_status.state // empty' <<< "$app_json")"
    if [[ "$compute_state" == "ACTIVE" ]]; then
      return 0
    fi
    if [[ "$compute_state" == "ERROR" || "$compute_state" == "FAILED" ]]; then
      printf 'Databricks App compute failed to start: %s\n' \
        "$(jq -r '.compute_status.message // "unknown error"' <<< "$app_json")" >&2
      exit 1
    fi
    sleep 5
  done

  printf 'Timed out waiting for Databricks App compute to become active: %s\n' "$app_name" >&2
  exit 1
}

databricks current-user me --output json >/dev/null
bundle_summary="$(databricks bundle summary -t "$target" --output json)"
app_source_path="$(jq -r '.resources.apps.supply_chain_agent.source_code_path // empty' <<< "$bundle_summary")"
if [[ -z "$app_source_path" ]]; then
  printf '%s\n' 'Bundle summary did not contain the supply_chain_agent Workspace Files source path.' >&2
  exit 1
fi

app_config_file="$(mktemp)"
trap 'rm -f "$app_config_file"' EXIT
printf '%s\n' \
  'command: ["uv", "run", "start-server"]' \
  'env:' \
  '  - name: MLFLOW_TRACKING_URI' \
  '    value: "databricks"' \
  '  - name: MLFLOW_REGISTRY_URI' \
  '    value: "databricks-uc"' \
  '  - name: MODEL_ENDPOINT' \
  "    value: \"${MODEL_ENDPOINT:-databricks-llama-4-maverick}\"" \
  '  - name: DATABRICKS_SQL_WAREHOUSE_ID' \
  "    value: \"${warehouse_id}\"" \
  '  - name: DBAI_CATALOG' \
  "    value: \"${catalog_name}\"" > "$app_config_file"
databricks workspace import "$app_source_path/app.yaml" \
  --file "$app_config_file" \
  --format AUTO \
  --overwrite \
  --output text >/dev/null

ensure_app_running
databricks apps deploy "$app_name" \
  --source-code-path "$app_source_path" \
  --skip-validation \
  --auto-approve

printf 'Databricks App deployed after Bootstrap: %s\n' "$app_name"
