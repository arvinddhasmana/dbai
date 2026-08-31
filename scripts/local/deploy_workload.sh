#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPOSITORY_ROOT"

if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' 'The jq command is required to locate the Bundle-uploaded App source.' >&2
  exit 1
fi

environment_name="${DBAI_ENVIRONMENT:-dev}"
target="${DBAI_BUNDLE_TARGET:-$environment_name}"
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
resource_group="${DBAI_RESOURCE_GROUP:-rg-dbai-${environment_name}}"
workspace_name="${DBAI_WORKSPACE_NAME:-dbai-${environment_name}}"
workspace_host="${DATABRICKS_HOST:-}"
catalog_name="${DBAI_CATALOG:?Set DBAI_CATALOG to the existing Unity Catalog catalog.}"
warehouse_id="${DATABRICKS_SQL_WAREHOUSE_ID:?Set DATABRICKS_SQL_WAREHOUSE_ID to the existing SQL Warehouse ID.}"
auth_mode="${DBAI_AUTH_MODE:-azure-cli}"

if [[ -z "$workspace_host" ]]; then
  if [[ -n "$subscription_id" ]]; then
    if ! az extension show --name databricks --only-show-errors >/dev/null 2>&1; then
      az extension add --name databricks --only-show-errors
    fi
    workspace_url="$(az databricks workspace show \
      --resource-group "$resource_group" \
      --name "$workspace_name" \
      --subscription "$subscription_id" \
      --query workspaceUrl \
      --output tsv)"
    workspace_host="https://${workspace_url#https://}"
  elif [[ -n "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
    workspace_host="$(databricks auth describe \
      --profile "$DATABRICKS_CONFIG_PROFILE" \
      --output json | jq -r '.details.host // empty')"
  else
    printf '%s\n' 'Set DATABRICKS_HOST, DATABRICKS_CONFIG_PROFILE, or AZURE_SUBSCRIPTION_ID so the workspace URL can be resolved.' >&2
    exit 1
  fi
fi

export DATABRICKS_HOST="$workspace_host"
export DATABRICKS_BUNDLE_VAR_sql_warehouse_id="$warehouse_id"
export DATABRICKS_BUNDLE_VAR_model_endpoint="${MODEL_ENDPOINT:-databricks-llama-4-maverick}"
export DATABRICKS_BUNDLE_VAR_ai_search_endpoint="${AI_SEARCH_ENDPOINT:-globalmart-supply-chain-search}"
export DBAI_CATALOG="$catalog_name"

case "$auth_mode" in
  azure-cli)
    export DATABRICKS_AUTH_TYPE=azure-cli
    ;;
  oauth-m2m)
    if [[ -z "${DATABRICKS_CLIENT_ID:-}" || -z "${DATABRICKS_CLIENT_SECRET:-}" ]]; then
      printf '%s\n' 'DBAI_AUTH_MODE=oauth-m2m requires DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET.' >&2
      exit 1
    fi
    export DATABRICKS_AUTH_TYPE=oauth-m2m
    ;;
  token)
    : "${DATABRICKS_TOKEN:?DBAI_AUTH_MODE=token requires DATABRICKS_TOKEN.}"
    unset DATABRICKS_AUTH_TYPE
    ;;
  *)
    printf 'Unsupported DBAI_AUTH_MODE: %s (use azure-cli, oauth-m2m, or token).\n' "$auth_mode" >&2
    exit 1
    ;;
esac

databricks current-user me --output json >/dev/null
databricks bundle validate -t "$target"
bundle_deploy_args=(
  bundle deploy
  -t "$target"
  "--var=sql_warehouse_id=${warehouse_id}"
  "--var=catalog=${catalog_name}"
  "--var=model_endpoint=${MODEL_ENDPOINT:-databricks-llama-4-maverick}"
  "--var=ai_search_endpoint=${AI_SEARCH_ENDPOINT:-globalmart-supply-chain-search}"
)
if ! databricks "${bundle_deploy_args[@]}"; then
  existing_app_name="dbai-${target}-supply-chain-agent"
  if databricks apps get "$existing_app_name" --output json >/dev/null 2>&1; then
    printf 'Binding existing App after Bundle state recovery: %s\n' "$existing_app_name"
    databricks bundle deployment bind supply_chain_agent "$existing_app_name" \
      -t "$target" \
      --auto-approve
    databricks "${bundle_deploy_args[@]}"
  else
    exit 1
  fi
fi

bundle_summary="$(databricks bundle summary -t "$target" --output json)"
app_name="$(jq -r '.resources.apps.supply_chain_agent.name // empty' <<< "$bundle_summary")"
app_source_path="$(jq -r '.resources.apps.supply_chain_agent.source_code_path // empty' <<< "$bundle_summary")"
if [[ -z "$app_name" || -z "$app_source_path" ]]; then
  printf '%s\n' 'Bundle summary did not contain the supply_chain_agent App name and Workspace Files source path.' >&2
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

databricks apps deploy "$app_name" \
  --source-code-path "$app_source_path" \
  --skip-validation \
  --auto-approve

printf 'Workload and App deployed. Bundle target: %s\n' "$target"
