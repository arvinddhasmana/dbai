#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPOSITORY_ROOT"

subscription_id="${AZURE_SUBSCRIPTION_ID:-bb2b8549-9693-40f2-9287-3bd5afcc6633}"
environment_name="${DBAI_ENVIRONMENT:-demo}"
resource_group="${DBAI_RESOURCE_GROUP:-rg-dbai-${environment_name}}"
managed_resource_group="${DBAI_MANAGED_RESOURCE_GROUP:-rg-dbai-${environment_name}-managed}"
workspace_name="${DBAI_WORKSPACE_NAME:-dbai-${environment_name}}"
warehouse_state_dir="${DBAI_STATE_DIR:-.dbai-state}"
warehouse_state_file="$warehouse_state_dir/${environment_name}-sql-warehouse-id"
profile="${DATABRICKS_CONFIG_PROFILE:-dbai-${environment_name}}"

if [[ "${1:-}" != "--yes" ]]; then
  printf '%s\n' "This deletes Databricks data-plane objects, the App, the workspace, and these dedicated Azure resource groups:"
  printf '  %s\n  %s\n' "$resource_group" "$managed_resource_group"
  printf '%s\n' "Run: scripts/local/destroy_demo_environment.sh --yes"
  exit 2
fi

for argument in "$@"; do
  if [[ "$argument" == "--dry-run" ]]; then
    /opt/az/bin/python3 scripts/local/destroy_demo_environment.py "$@"
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
  export DATABRICKS_CONFIG_PROFILE="$profile"
  export DATABRICKS_HOST="$workspace_url"
  if [[ -z "${DATABRICKS_TOKEN:-}" ]]; then
    databricks auth login --profile "$profile" --host "$workspace_url"
  fi
  databricks current-user me -p "$profile" --output json >/dev/null
  /opt/az/bin/python3 scripts/local/destroy_demo_environment.py --yes "$@"
  if [[ -f "$warehouse_state_file" ]]; then
    created_warehouse_id="$(<"$warehouse_state_file")"
    if [[ -n "$created_warehouse_id" ]]; then
      databricks warehouses delete "$created_warehouse_id" -p "$profile"
    fi
    rm -f "$warehouse_state_file"
  fi
fi

az group delete --name "$resource_group" --subscription "$subscription_id" --yes

if az group show --name "$managed_resource_group" --subscription "$subscription_id" >/dev/null 2>&1; then
  az group delete --name "$managed_resource_group" --subscription "$subscription_id" --yes
fi

printf '%s\n' "Demo Azure resources destroyed. The existing rgdata resource group was not touched."