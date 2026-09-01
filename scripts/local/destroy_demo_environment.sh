#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPOSITORY_ROOT"

subscription_id="${AZURE_SUBSCRIPTION_ID:-bb2b8549-9693-40f2-9287-3bd5afcc6633}"
environment_name="${DBAI_ENVIRONMENT:-demo}"
resource_group_prefix="rg-dbai-${environment_name}"
resource_group="${DBAI_RESOURCE_GROUP:-${resource_group_prefix}}"
managed_resource_group="${DBAI_MANAGED_RESOURCE_GROUP:-${resource_group_prefix}-managed}"
workspace_name="${DBAI_WORKSPACE_NAME:-dbai-${environment_name}}"
warehouse_state_dir="${DBAI_STATE_DIR:-.dbai-state}"
warehouse_state_file="$warehouse_state_dir/${environment_name}-sql-warehouse-id"
profile="${DATABRICKS_CONFIG_PROFILE:-dbai-${environment_name}}"
cleanup_local_config=false
python_executable="${DBAI_PYTHON:-}"

if [[ ! "$environment_name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  printf 'DBAI_ENVIRONMENT must use lowercase letters, numbers, and hyphens: %s\n' "$environment_name" >&2
  exit 1
fi

resource_group_pattern="^${resource_group_prefix}(-[a-z0-9]+)*$"
managed_resource_group_pattern="^${resource_group_prefix}(-[a-z0-9]+)*-managed$"

if [[ ! "$resource_group" =~ $resource_group_pattern ]]; then
  printf 'DBAI_RESOURCE_GROUP must be deployment-owned and start with %s: %s\n' "$resource_group_prefix" "$resource_group" >&2
  exit 1
fi

if [[ ! "$managed_resource_group" =~ $managed_resource_group_pattern ]]; then
  printf 'DBAI_MANAGED_RESOURCE_GROUP must be deployment-owned and use the managed suffix: %s\n' "$managed_resource_group" >&2
  exit 1
fi

if [[ "$resource_group" == *-managed || "$resource_group" == "$managed_resource_group" ]]; then
  printf 'The primary and managed resource groups must be distinct deployment-owned groups.\n' >&2
  exit 1
fi

if [[ -z "$python_executable" ]]; then
  if [[ -x "$REPOSITORY_ROOT/.venv/bin/python" ]]; then
    python_executable="$REPOSITORY_ROOT/.venv/bin/python"
  else
    python_executable="/opt/az/bin/python3"
  fi
fi

for argument in "$@"; do
  if [[ "$argument" == "--cleanup-local-config" ]]; then
    cleanup_local_config=true
  fi
done

if ! printf '%s\n' "$@" | grep -qx -- '--yes'; then
  printf '%s\n' "This deletes Databricks data-plane objects, the App, the workspace, and these deployment-owned Azure resource groups:"
  printf '  %s\n  %s\n' "$resource_group" "$managed_resource_group"
  printf '%s\n' "Run: scripts/local/destroy_demo_environment.sh --yes"
  exit 2
fi

for argument in "$@"; do
  if [[ "$argument" == "--dry-run" ]]; then
    "$python_executable" scripts/local/destroy_demo_environment.py "$@"
    exit 0
  fi
done

workspace_exists="$(az databricks workspace show \
  --resource-group "$resource_group" \
  --name "$workspace_name" \
  --subscription "$subscription_id" \
  --query id \
  --output tsv 2>/dev/null || true)"

if [[ -n "$workspace_exists" ]]; then
  workspace_url="$(az databricks workspace show \
    --resource-group "$resource_group" \
    --name "$workspace_name" \
    --subscription "$subscription_id" \
    --query workspaceUrl \
    --output tsv)"
  case "$workspace_url" in
    http://*|https://*) ;;
    *) workspace_url="https://$workspace_url" ;;
  esac
  export DATABRICKS_CONFIG_PROFILE="$profile"
  export DATABRICKS_HOST="$workspace_url"
  if [[ -z "${DATABRICKS_TOKEN:-}" ]]; then
    databricks auth login --profile "$profile" --host "$workspace_url"
  fi
  databricks current-user me -p "$profile" --output json >/dev/null
  "$python_executable" scripts/local/destroy_demo_environment.py --yes "$@"
  if [[ -f "$warehouse_state_file" ]]; then
    created_warehouse_id="$(<"$warehouse_state_file")"
    if [[ -n "$created_warehouse_id" ]]; then
      databricks warehouses delete "$created_warehouse_id" -p "$profile"
    fi
    rm -f "$warehouse_state_file"
  fi
fi

if az group show --name "$resource_group" --subscription "$subscription_id" >/dev/null 2>&1; then
  az group delete --name "$resource_group" --subscription "$subscription_id" --yes
fi

if az group show --name "$managed_resource_group" --subscription "$subscription_id" >/dev/null 2>&1; then
  az group delete --name "$managed_resource_group" --subscription "$subscription_id" --yes
fi

printf 'Azure resources for environment %s destroyed. Only deployment-owned rg-dbai-* resource groups were targeted.\n' \
  "$environment_name"

if [[ "$cleanup_local_config" == true ]]; then
  scripts/local/cleanup_local_databricks.sh \
    --environment "$environment_name" \
    --target "${DBAI_BUNDLE_TARGET:-$environment_name}" \
    --yes
fi