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
databricks bundle deploy -t "$target" \
  "--var=sql_warehouse_id=${warehouse_id}" \
  "--var=catalog=${catalog_name}" \
  "--var=model_endpoint=${MODEL_ENDPOINT:-databricks-llama-4-maverick}" \
  "--var=ai_search_endpoint=${AI_SEARCH_ENDPOINT:-globalmart-supply-chain-search}"

ensure_app_running
databricks bundle run supply_chain_contract_agent -t "$target" --restart \
  "--var=sql_warehouse_id=${warehouse_id}" \
  "--var=catalog=${catalog_name}" \
  "--var=model_endpoint=${MODEL_ENDPOINT:-databricks-llama-4-maverick}"

printf 'Databricks App restarted from Bundle configuration: %s\n' "$app_name"
