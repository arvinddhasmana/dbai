#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPOSITORY_ROOT"

python_bin="${DBAI_PYTHON:-}"
if [[ -z "$python_bin" ]]; then
  if [[ -x "$REPOSITORY_ROOT/.venv/bin/python" ]]; then
    python_bin="$REPOSITORY_ROOT/.venv/bin/python"
  else
    python_bin="$(command -v python3)"
  fi
fi

if ! "$python_bin" -c 'import databricks' >/dev/null 2>&1; then
  printf 'Python interpreter %s is missing the Databricks SDK. Install requirements-dev.txt or set DBAI_PYTHON.\n' "$python_bin" >&2
  exit 1
fi

subscription_id="${AZURE_SUBSCRIPTION_ID:-bb2b8549-9693-40f2-9287-3bd5afcc6633}"
location="${AZURE_LOCATION:-eastus2}"
environment_name="${DBAI_ENVIRONMENT:-demo}"
resource_group="${DBAI_RESOURCE_GROUP:-rg-dbai-${environment_name}}"
managed_resource_group="${DBAI_MANAGED_RESOURCE_GROUP:-rg-dbai-${environment_name}-managed}"
warehouse_id="${DATABRICKS_SQL_WAREHOUSE_ID:-}"
warehouse_name="${DBAI_SQL_WAREHOUSE_NAME:-dbai-${environment_name}-sql}"
warehouse_state_dir="${DBAI_STATE_DIR:-.dbai-state}"
warehouse_state_file="$warehouse_state_dir/${environment_name}-sql-warehouse-id"
catalog_name="${DBAI_CATALOG:-}"
catalog_state_file="$warehouse_state_dir/${environment_name}-catalog"
profile="${DATABRICKS_CONFIG_PROFILE:-dbai-${environment_name}}"
auth_mode="${DBAI_AUTH_MODE:-}"

if ! az extension show --name databricks --only-show-errors >/dev/null 2>&1; then
  az extension add --name databricks --only-show-errors
fi

az account set --subscription "$subscription_id"
az bicep build --file infra/main.bicep --stdout >/dev/null
az deployment sub create \
  --name "dbai-${environment_name}-$(date +%Y%m%d%H%M%S)" \
  --location "$location" \
  --template-file infra/main.bicep \
  --parameters environmentName="$environment_name" \
               resourceGroupName="$resource_group" \
               managedResourceGroupName="$managed_resource_group" \
               location="$location" \
  --subscription "$subscription_id" \
  --output none

workspace_url="$(az databricks workspace show \
  --resource-group "$resource_group" \
  --name "dbai-${environment_name}" \
  --subscription "$subscription_id" \
  --query workspaceUrl \
  --output tsv)"

if [[ -z "$workspace_url" ]]; then
  printf '%s\n' "Azure Databricks workspace URL was not returned." >&2
  exit 1
fi

workspace_host="https://${workspace_url#https://}"
export DATABRICKS_CONFIG_PROFILE="$profile"
export DATABRICKS_HOST="$workspace_host"
export DATABRICKS_BUNDLE_VAR_sql_warehouse_id="$warehouse_id"
export DBAI_CATALOG="$catalog_name"

if [[ -z "$auth_mode" && -n "${DATABRICKS_CLIENT_ID:-}" && -n "${DATABRICKS_CLIENT_SECRET:-}" ]]; then
  auth_mode="oauth-m2m"
fi

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
  '')
    ;;
  *)
    printf 'Unsupported DBAI_AUTH_MODE: %s (use azure-cli or oauth-m2m).\n' "$auth_mode" >&2
    exit 1
    ;;
esac

if [[ -z "${DATABRICKS_TOKEN:-}" ]] && ! databricks current-user me -p "$profile" --output json >/dev/null 2>&1; then
  if [[ -n "$auth_mode" ]]; then
    printf 'Authentication mode %s could not authenticate to %s.\n' "$auth_mode" "$workspace_host" >&2
    exit 1
  fi
  printf 'Authenticate profile %s to %s, then return here.\n' "$profile" "$workspace_host"
  databricks auth logout --profile "$profile" --delete --auto-approve >/dev/null 2>&1 || true
  databricks auth login --profile "$profile" --host "$workspace_host"
fi

databricks current-user me -p "$profile" --output json >/dev/null

if [[ -z "$catalog_name" ]]; then
  catalog_name="$(databricks catalogs list -p "$profile" -o json | python3 -c \
    'import json, sys; environment = sys.argv[1]; candidates = [item["name"] for item in json.load(sys.stdin) if item.get("catalog_type") == "MANAGED_CATALOG" and item.get("isolation_mode") == "ISOLATED" and item.get("name", "").startswith(f"dbai_{environment}_")]; print(candidates[0] if len(candidates) == 1 else "")' \
    "$environment_name")"
  if [[ -z "$catalog_name" ]]; then
    printf 'Could not identify the workspace-specific isolated catalog. Set DBAI_CATALOG explicitly.\n' >&2
    exit 1
  fi
fi

export DBAI_CATALOG="$catalog_name"
mkdir -p "$warehouse_state_dir"
printf '%s\n' "$catalog_name" > "$catalog_state_file"

if [[ -z "$warehouse_id" ]]; then
  warehouse_id="$(databricks warehouses list -p "$profile" -o json | python3 -c \
    'import json, sys; name = sys.argv[1]; items = json.load(sys.stdin); print(next((item["id"] for item in items if item.get("name") == name), ""))' \
    "$warehouse_name")"
  if [[ -z "$warehouse_id" ]]; then
    warehouse_id="$(databricks warehouses create \
      -p "$profile" \
      --name "$warehouse_name" \
      --cluster-size "2X-Small" \
      --min-num-clusters 1 \
      --max-num-clusters 1 \
      --auto-stop-mins 10 \
      --enable-serverless-compute \
      --warehouse-type PRO \
      -o json | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')"
    mkdir -p "$warehouse_state_dir"
    printf '%s\n' "$warehouse_id" > "$warehouse_state_file"
  fi
fi

export DATABRICKS_SQL_WAREHOUSE_ID="$warehouse_id"
export MODEL_ENDPOINT="${MODEL_ENDPOINT:-databricks-llama-4-maverick}"
export AI_SEARCH_ENDPOINT="${AI_SEARCH_ENDPOINT:-globalmart-supply-chain-search}"
export DBAI_BUNDLE_TARGET="${DBAI_BUNDLE_TARGET:-dev}"
export DBAI_APP_NAME="${DBAI_APP_NAME:-dbai-${DBAI_BUNDLE_TARGET}-supply-chain-agent}"
if [[ -z "${DBAI_APP_USER:-}" ]]; then
  DBAI_APP_USER="$(databricks current-user me -p "$profile" -o json | python3 -c \
    'import json, sys; user = json.load(sys.stdin); print(user.get("userName") or user.get("user_name") or "")')"
  export DBAI_APP_USER
fi

scripts/local/deploy_workload.sh
"$python_bin" scripts/local/bootstrap_demo_environment.py \
  --target "$DBAI_BUNDLE_TARGET" \
  --warehouse-id "$warehouse_id" \
  --skip-deploy \
  --app-name "$DBAI_APP_NAME" \
  --user-principal "$DBAI_APP_USER" \
  --bootstrap-principal "$DBAI_APP_USER"
scripts/local/deploy_app.sh

printf '\nDeployment complete. Workspace: %s\n' "$workspace_url"
printf 'SQL warehouse: %s\n' "$warehouse_id"
printf '%s\n' "Wait for the TRIGGERED AI Search index to become Online and its initial sync to become Completed before testing retrieval."