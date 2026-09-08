#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AGENT_ROOT="$REPOSITORY_ROOT/agents/supply_chain_agent"
cd "$AGENT_ROOT"

if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' 'The jq command is required to inspect the Databricks App state.' >&2
  exit 1
fi

target="${DBAI_BUNDLE_TARGET:-${DBAI_ENVIRONMENT:-dev}}"
catalog_name="${DBAI_CATALOG:?Set DBAI_CATALOG to the existing Unity Catalog catalog.}"
warehouse_id="${DATABRICKS_SQL_WAREHOUSE_ID:?Set DATABRICKS_SQL_WAREHOUSE_ID to the existing SQL Warehouse ID.}"
app_name="${DBAI_APP_NAME:-dbai-supply-agent-${target}}"
app_resource_key="${DBAI_APP_RESOURCE_KEY:-supply_chain_contract_agent}"
mlflow_experiment_name="${MLFLOW_EXPERIMENT_NAME:-/Shared/globalmart-supply-chain-agent-uc-${target}}"

if [[ "$mlflow_experiment_name" != /* ]]; then
  printf 'MLFLOW_EXPERIMENT_NAME must be a workspace-absolute path such as /Shared/globalmart-supply-chain-agent-%s\n' "$target" >&2
  exit 1
fi

if [[ -n "${DBAI_CLI_BIN:-}" ]]; then
  if [[ ! -x "$DBAI_CLI_BIN" ]]; then
    printf 'DBAI_CLI_BIN is not executable: %s\n' "$DBAI_CLI_BIN" >&2
    exit 1
  fi
  databricks() { "$DBAI_CLI_BIN" "$@"; }
fi

cli_version="$(databricks version | sed -n 's/^Databricks CLI v//p')"
if [[ -z "$cli_version" ]]; then
  printf '%s\n' 'Could not determine the Databricks CLI version.' >&2
  exit 1
fi

native_bundle_deploy_supported=true
if [[ "$(printf '%s\n' '1.15.0' "$cli_version" | sort -V | head -n 1)" != '1.15.0' ]]; then
  native_bundle_deploy_supported=false
  printf 'Databricks CLI %s has the Apps update-mask regression; using the compatibility fallback. Upgrade to v1.15.0 or newer for native Bundle deployment.\n' "$cli_version" >&2
fi

workspace_host="${DATABRICKS_HOST:-}"
if [[ -z "$workspace_host" && -n "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
  workspace_host="$(databricks auth describe \
    --profile "$DATABRICKS_CONFIG_PROFILE" \
    --output json | jq -r '.details.host // empty')"
fi
if [[ "$workspace_host" =~ adb-([0-9]+)\. ]]; then
  export DATABRICKS_WORKSPACE_ID="${BASH_REMATCH[1]}"
fi

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
databricks bundle validate -t "$target"
bundle_vars=(
  "--var=sql_warehouse_id=${warehouse_id}"
  "--var=catalog=${catalog_name}"
  "--var=model_endpoint=${MODEL_ENDPOINT:-databricks-llama-4-maverick}"
  "--var=mlflow_experiment_name=${mlflow_experiment_name}"
  "--var=mlflow_trace_catalog=${MLFLOW_TRACE_CATALOG:-${catalog_name}}"
  "--var=mlflow_trace_schema=${MLFLOW_TRACE_SCHEMA:-supply_chain}"
  "--var=mlflow_trace_table_prefix=${MLFLOW_TRACE_TABLE_PREFIX:-contract_agent_traces}"
)

if [[ "$native_bundle_deploy_supported" == true ]]; then
  databricks bundle deploy -t "$target" "${bundle_vars[@]}"
  databricks bundle run "$app_resource_key" -t "$target" --restart "${bundle_vars[@]}"
  printf 'Databricks App deployed and restarted through the Bundle: %s\n' "$app_name"
  exit 0
fi

databricks bundle sync -t "$target" "${bundle_vars[@]}"
ensure_app_running
app_source_path="$(databricks bundle summary -t "$target" --output json \
  | jq -r '.workspace.file_path // empty')"
if [[ -z "$app_source_path" ]]; then
  printf 'Could not resolve the Bundle workspace file path for App deployment.\n' >&2
  exit 1
fi
databricks apps deploy "$app_name" \
  --source-code-path "$app_source_path" \
  --skip-validation \
  --auto-approve

printf 'Databricks App deployed from compatibility fallback: %s\n' "$app_name"
