#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPOSITORY_ROOT"

environment_name="${DBAI_ENVIRONMENT:-dev}"
target="${DBAI_BUNDLE_TARGET:-$environment_name}"
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
resource_group="${DBAI_RESOURCE_GROUP:-rg-dbai-${environment_name}}"
workspace_name="${DBAI_WORKSPACE_NAME:-dbai-${environment_name}}"
workspace_host="${DATABRICKS_HOST:-}"
workspace_id="${DATABRICKS_WORKSPACE_ID:-}"
catalog_name="${DBAI_CATALOG:?Set DBAI_CATALOG to the existing Unity Catalog catalog.}"
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

if [[ -z "$workspace_id" && "$workspace_host" =~ adb-([0-9]+)\. ]]; then
  workspace_id="${BASH_REMATCH[1]}"
fi
if [[ -n "$workspace_id" ]]; then
  export DATABRICKS_WORKSPACE_ID="$workspace_id"
fi

export DATABRICKS_HOST="$workspace_host"
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
  "--var=catalog=${catalog_name}"
)
if ! databricks "${bundle_deploy_args[@]}"; then
  exit 1
fi

printf 'Shared platform workload deployed. Agent deployment is performed by Bootstrap after AI Search setup. Target: %s\n' "$target"
