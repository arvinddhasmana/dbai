#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPOSITORY_ROOT"

usage() {
  cat <<'EOF'
Usage: scripts/local/configure_databricks_environment.sh [options]

Configure one Databricks environment for GitHub Actions. Run this as a
Databricks account administrator and workspace administrator.

Options:
  --environment NAME          Environment: dev, test, or prod (required)
  --repo OWNER/REPO           GitHub repository (default: current repository)
  --subscription-id ID        Azure subscription (default: current subscription)
  --deployment-client-id ID   Entra application/client ID used by GitHub Actions
                              (default: 16f08498-e32f-4650-9cfe-09ef6988f602)
  --account-profile NAME      Databricks account-admin CLI profile
                              (default: DATABRICKS_ACCOUNT_PROFILE or account-admin)
  --workspace-profile NAME    Databricks workspace-admin CLI profile
                              (default: DATABRICKS_WORKSPACE_PROFILE or
                              dbai-ENVIRONMENT-admin)
  --catalog NAME              Existing catalog (default: discover isolated catalog)
  --warehouse-id ID           Existing SQL Warehouse ID
  --warehouse-name NAME       SQL Warehouse name to discover/create
                              (default: dbai-ENVIRONMENT-sql)
  --app-user PRINCIPAL        Databricks user for App on-behalf-of requests
                              (default: current workspace profile user)
  --model-endpoint NAME       Model-serving endpoint to grant CAN_QUERY
                              (default: databricks-llama-4-maverick)
  --model-endpoint-id ID      UUID required by the serving permissions API
  --ai-search-endpoint NAME   AI Search endpoint to grant CAN_USE when present
                              (default: globalmart-supply-chain-search)
  --ai-search-endpoint-id ID  UUID required by the vector-search permissions API
  --help                      Show this help

The script writes DBAI_CATALOG and DATABRICKS_SQL_WAREHOUSE_ID only to the
selected GitHub Environment. It does not create secrets or workspace-admin
roles for the deployment service principal.
EOF
}

environment_name=""
repo=""
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
deployment_client_id="${DBAI_DEPLOYMENT_CLIENT_ID:-16f08498-e32f-4650-9cfe-09ef6988f602}"
account_profile="${DATABRICKS_ACCOUNT_PROFILE:-account-admin}"
workspace_profile="${DATABRICKS_WORKSPACE_PROFILE:-${DATABRICKS_CONFIG_PROFILE:-}}"
catalog_override="${DBAI_CATALOG:-}"
warehouse_id="${DATABRICKS_SQL_WAREHOUSE_ID:-}"
warehouse_name="${DBAI_SQL_WAREHOUSE_NAME:-}"
app_user="${DBAI_APP_USER:-}"
model_endpoint="${MODEL_ENDPOINT:-databricks-llama-4-maverick}"
model_endpoint_id="${MODEL_ENDPOINT_ID:-}"
ai_search_endpoint="${AI_SEARCH_ENDPOINT:-globalmart-supply-chain-search}"
ai_search_endpoint_id="${AI_SEARCH_ENDPOINT_ID:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment)
      environment_name="$2"
      shift 2
      ;;
    --repo)
      repo="$2"
      shift 2
      ;;
    --subscription-id)
      subscription_id="$2"
      shift 2
      ;;
    --deployment-client-id)
      deployment_client_id="$2"
      shift 2
      ;;
    --account-profile)
      account_profile="$2"
      shift 2
      ;;
    --workspace-profile)
      workspace_profile="$2"
      shift 2
      ;;
    --catalog)
      catalog_override="$2"
      shift 2
      ;;
    --warehouse-id)
      warehouse_id="$2"
      shift 2
      ;;
    --warehouse-name)
      warehouse_name="$2"
      shift 2
      ;;
    --app-user)
      app_user="$2"
      shift 2
      ;;
    --model-endpoint)
      model_endpoint="$2"
      shift 2
      ;;
    --model-endpoint-id)
      model_endpoint_id="$2"
      shift 2
      ;;
    --ai-search-endpoint)
      ai_search_endpoint="$2"
      shift 2
      ;;
    --ai-search-endpoint-id)
      ai_search_endpoint_id="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "$environment_name" =~ ^(dev|test|prod)$ ]]; then
  printf '%s\n' '--environment must be dev, test, or prod.' >&2
  exit 1
fi

if [[ -z "${DATABRICKS_WORKSPACE_PROFILE:-}" && -z "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
  workspace_profile="dbai-${environment_name}-admin"
fi

for command in az databricks gh jq; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Required command is missing: %s\n' "$command" >&2
    exit 1
  fi
done

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

if [[ -z "$repo" ]]; then
  repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
fi
if [[ ! "$repo" =~ ^[^/]+/[^/]+$ ]]; then
  printf 'Repository must be OWNER/REPO: %s\n' "$repo" >&2
  exit 1
fi

if [[ -z "$subscription_id" ]]; then
  subscription_id="$(az account show --query id --output tsv)"
fi
az account set --subscription "$subscription_id" --only-show-errors

resource_group="${DBAI_RESOURCE_GROUP:-rg-dbai-${environment_name}}"
workspace_name="${DBAI_WORKSPACE_NAME:-dbai-${environment_name}}"
if [[ -z "$warehouse_name" ]]; then
  warehouse_name="dbai-${environment_name}-sql"
fi

workspace_json="$(az databricks workspace show \
  --resource-group "$resource_group" \
  --name "$workspace_name" \
  --subscription "$subscription_id" \
  --query '{workspaceId:workspaceId,workspaceUrl:workspaceUrl}' \
  --output json)"
workspace_id="$(jq -r '.workspaceId // empty' <<< "$workspace_json")"
workspace_url="$(jq -r '.workspaceUrl // empty' <<< "$workspace_json")"
if [[ -z "$workspace_id" || -z "$workspace_url" ]]; then
  printf 'Could not resolve the deployed workspace %s in %s.\n' "$workspace_name" "$resource_group" >&2
  exit 1
fi
workspace_host="https://${workspace_url#https://}"

account_json="$(databricks account service-principals list --profile "$account_profile" --output json)"
account_sp_id="$(jq -r --arg application_id "$deployment_client_id" '
  (if type == "array" then . else (.Resources // .resources // []) end)
  | map(select((.applicationId // .application_id // "") == $application_id))
  | .[0].id // empty
' <<< "$account_json")"
if [[ -z "$account_sp_id" ]]; then
  account_sp_json="$(databricks account service-principals create \
    --application-id "$deployment_client_id" \
    --display-name dbai-github-actions \
    --profile "$account_profile" \
    --output json)"
  account_sp_id="$(jq -r '.id // empty' <<< "$account_sp_json")"
  if [[ -z "$account_sp_id" ]]; then
    printf 'Databricks account service principal creation returned no ID.\n' >&2
    exit 1
  fi
  printf 'Registered deployment service principal in Databricks account: %s\n' "$account_sp_id"
else
  printf 'Deployment service principal is already registered: %s\n' "$account_sp_id"
fi

databricks account workspace-assignment update "$workspace_id" "$account_sp_id" \
  --json '{"permissions":["USER"]}' \
  --profile "$account_profile" \
  --output text >/dev/null
printf 'Assigned deployment service principal to workspace %s.\n' "$workspace_id"

export DATABRICKS_CONFIG_PROFILE="$workspace_profile"
export DATABRICKS_HOST="$workspace_host"

if [[ -z "$app_user" ]]; then
  current_user_json="$(databricks current-user me --profile "$workspace_profile" --output json)"
  app_user="$(jq -r '(.userName // .user_name // ((.emails // [])[] | select(.primary == true) | .value) // empty)' <<< "$current_user_json")"
fi
if [[ -z "$app_user" ]]; then
  printf '%s\n' 'Could not resolve the Databricks user for App on-behalf-of access. Pass --app-user.' >&2
  exit 1
fi
printf 'App on-behalf-of user: %s\n' "$app_user"

workspace_sp_json="$(databricks service-principals list --profile "$workspace_profile" --output json)"
workspace_sp_id="$(jq -r --arg application_id "$deployment_client_id" '
  (if type == "array" then . else (.Resources // .resources // []) end)
  | map(select((.applicationId // .application_id // "") == $application_id))
  | .[0].id // empty
' <<< "$workspace_sp_json")"
if [[ -z "$workspace_sp_id" ]]; then
  printf 'The deployment service principal is not visible in workspace %s.\n' "$workspace_host" >&2
  printf 'Confirm the account assignment propagated and that --workspace-profile is a workspace administrator profile.\n' >&2
  exit 1
fi

workspace_sp_record="$(jq -c --arg application_id "$deployment_client_id" '
  (if type == "array" then . else (.Resources // .resources // []) end)
  | map(select((.applicationId // .application_id // "") == $application_id))
  | .[0] // {}
' <<< "$workspace_sp_json")"
for entitlement in workspace-access databricks-sql-access; do
  if ! jq -e --arg entitlement "$entitlement" '(.entitlements // []) | any(.value == $entitlement)' <<< "$workspace_sp_record" >/dev/null; then
    entitlement_patch="$(jq -cn --arg entitlement "$entitlement" '{schemas:["urn:ietf:params:scim:api:messages:2.0:PatchOp"],Operations:[{op:"add",path:"entitlements",value:[{value:$entitlement}]}]}')"
    databricks api patch "/api/2.0/preview/scim/v2/ServicePrincipals/${workspace_sp_id}" \
      --json "$entitlement_patch" \
      --profile "$workspace_profile" \
      --output text >/dev/null
  fi
done
printf 'Workspace entitlements verified for deployment service principal.\n'

catalog_name="$catalog_override"
if [[ -z "$catalog_name" ]]; then
  catalogs_json="$(databricks catalogs list --profile "$workspace_profile" --output json)"
  catalog_name="$(jq -r --arg prefix "dbai_${environment_name}" '
    (if type == "array" then . else (.catalogs // .Resources // .resources // []) end)
    | map(select((.catalog_type // "") == "MANAGED_CATALOG" and (.isolation_mode // "") == "ISOLATED" and ((.name // "") == $prefix or (.name // "" | startswith($prefix + "_")))))
    | .[0].name // empty
  ' <<< "$catalogs_json")"
fi
if [[ -z "$catalog_name" ]]; then
  printf 'Could not identify an isolated catalog for %s. Re-run with --catalog <catalog-name>.\n' "$environment_name" >&2
  exit 1
fi
printf 'Using Unity Catalog: %s\n' "$catalog_name"

if [[ -z "$warehouse_id" ]]; then
  warehouses_json="$(databricks warehouses list --profile "$workspace_profile" --output json)"
  warehouse_id="$(jq -r --arg name "$warehouse_name" '
    (if type == "array" then . else (.warehouses // .Resources // .resources // []) end)
    | map(select(.name == $name))
    | .[0].id // empty
  ' <<< "$warehouses_json")"
fi
if [[ -z "$warehouse_id" ]]; then
  warehouse_json="$(databricks warehouses create \
    --name "$warehouse_name" \
    --cluster-size 2X-Small \
    --min-num-clusters 1 \
    --max-num-clusters 1 \
    --auto-stop-mins 10 \
    --enable-serverless-compute \
    --warehouse-type PRO \
    --no-wait \
    --profile "$workspace_profile" \
    --output json)"
  warehouse_id="$(jq -r '.id // empty' <<< "$warehouse_json")"
  if [[ -z "$warehouse_id" ]]; then
    printf 'SQL Warehouse creation returned no ID.\n' >&2
    exit 1
  fi
  printf 'Created SQL Warehouse: %s (%s)\n' "$warehouse_name" "$warehouse_id"
else
  printf 'Using SQL Warehouse: %s (%s)\n' "$warehouse_name" "$warehouse_id"
fi

warehouse_acl="$(jq -cn --arg principal "$deployment_client_id" --arg user "$app_user" \
  '{access_control_list:([{service_principal_name:$principal,permission_level:"CAN_USE"}] + [{user_name:$user,permission_level:"CAN_USE"}])}')"
databricks warehouses update-permissions "$warehouse_id" \
  --json "$warehouse_acl" \
  --profile "$workspace_profile" \
  --output text >/dev/null
printf 'Granted CAN_USE on SQL Warehouse.\n'

sql_statements="$(jq -cn \
  --arg catalog "$catalog_name" \
  --arg principal "$deployment_client_id" \
  --arg user "$app_user" \
  '[
    ("GRANT USE CATALOG ON CATALOG `" + $catalog + "` TO `" + $principal + "`"),
    ("GRANT CREATE SCHEMA ON CATALOG `" + $catalog + "` TO `" + $principal + "`"),
    ("CREATE SCHEMA IF NOT EXISTS `" + $catalog + "`.supply_chain"),
    ("GRANT USE SCHEMA ON SCHEMA `" + $catalog + "`.supply_chain TO `" + $principal + "`"),
    ("GRANT CREATE TABLE ON SCHEMA `" + $catalog + "`.supply_chain TO `" + $principal + "`"),
    ("GRANT CREATE VOLUME ON SCHEMA `" + $catalog + "`.supply_chain TO `" + $principal + "`"),
    ("GRANT CREATE FUNCTION ON SCHEMA `" + $catalog + "`.supply_chain TO `" + $principal + "`"),
    ("CREATE VOLUME IF NOT EXISTS `" + $catalog + "`.supply_chain.vendor_contracts"),
    ("GRANT READ VOLUME, WRITE VOLUME ON VOLUME `" + $catalog + "`.supply_chain.vendor_contracts TO `" + $principal + "`")
  ] + [
    ("GRANT USE CATALOG ON CATALOG `" + $catalog + "` TO `" + ($user | gsub("`"; "``")) + "`"),
    ("GRANT USE SCHEMA ON SCHEMA `" + $catalog + "`.supply_chain TO `" + ($user | gsub("`"; "``")) + "`")
    ("GRANT USE SCHEMA ON SCHEMA `" + ($catalog | gsub("`"; "``")) + "`.supply_chain TO `" + ($user | gsub("`"; "``")) + "`"),
    ("GRANT CREATE TABLE ON SCHEMA `" + ($catalog | gsub("`"; "``")) + "`.supply_chain TO `" + ($user | gsub("`"; "``")) + "`")
  ]')"
export DBAI_SQL_STATEMENTS="$sql_statements"
export DBAI_SQL_WAREHOUSE_ID="$warehouse_id"
"$python_bin" - <<'PY'
import json
import os
import time

from databricks.sdk import WorkspaceClient

client = WorkspaceClient()
warehouse_id = os.environ["DBAI_SQL_WAREHOUSE_ID"]
for statement in json.loads(os.environ["DBAI_SQL_STATEMENTS"]):
    response = client.statement_execution.execute_statement(
        statement,
        warehouse_id=warehouse_id,
        wait_timeout="30s",
    )
    while str(getattr(response.status.state, "value", response.status.state)).rsplit(".", 1)[-1] in {"PENDING", "RUNNING"}:
        time.sleep(2)
        response = client.statement_execution.get_statement(response.statement_id)
    state = str(getattr(response.status.state, "value", response.status.state)).rsplit(".", 1)[-1]
    if state != "SUCCEEDED":
        error = getattr(response.status, "error", None)
        message = getattr(error, "message", None) or str(error) or "unknown SQL error"
        raise SystemExit(f"SQL statement failed ({state}): {message}\n{statement}")
PY
printf 'Granted Unity Catalog permissions and prepared supply_chain.vendor_contracts.\n'

"$python_bin" scripts/local/grant_data_access.py \
  --catalog "$catalog_name" \
  --warehouse-id "$warehouse_id" \
  --prepare-bootstrap \
  --bootstrap-principal "$deployment_client_id" \
  --bootstrap-principal "$app_user"
printf 'Prepared bootstrap MODIFY permissions for existing Gold tables.\n'

serving_json="$(databricks serving-endpoints list --profile "$workspace_profile" --output json 2>/dev/null || printf '[]')"
if [[ -n "$model_endpoint_id" ]]; then
  serving_acl="$(jq -cn --arg principal "$deployment_client_id" \
    '{access_control_list:[{service_principal_name:$principal,permission_level:"CAN_QUERY"}]}')"
  databricks permissions update serving-endpoints "$model_endpoint_id" \
    --json "$serving_acl" \
    --profile "$workspace_profile" \
    --output text >/dev/null
  printf 'Granted CAN_QUERY on model endpoint ID: %s\n' "$model_endpoint_id"
elif jq -e --arg name "$model_endpoint" '
  (if type == "array" then . else (.endpoints // .Resources // .resources // []) end)
  | any(.name == $name)
' <<< "$serving_json" >/dev/null; then
  printf 'Model endpoint exists but its permissions API requires a UUID; skipped CAN_QUERY grant for %s. Use --model-endpoint-id.\n' "$model_endpoint"
else
  printf 'Model endpoint not found; skipped CAN_QUERY grant: %s\n' "$model_endpoint"
fi

if [[ -n "$ai_search_endpoint_id" ]] && databricks permissions get vector-search-endpoints "$ai_search_endpoint_id" \
  --profile "$workspace_profile" \
  --output json >/dev/null 2>&1; then
  search_acl="$(jq -cn --arg principal "$deployment_client_id" \
    '{access_control_list:[{service_principal_name:$principal,permission_level:"CAN_USE"}]}')"
  databricks permissions update vector-search-endpoints "$ai_search_endpoint_id" \
    --json "$search_acl" \
    --profile "$workspace_profile" \
    --output text >/dev/null
  printf 'Granted CAN_USE on AI Search endpoint ID: %s\n' "$ai_search_endpoint_id"
elif [[ -n "$ai_search_endpoint_id" ]]; then
  printf 'AI Search endpoint ID not found; skipped CAN_USE grant: %s\n' "$ai_search_endpoint_id"
else
  printf 'AI Search endpoint UUID not supplied; skipped CAN_USE grant for %s. Bootstrap will create or configure it.\n' "$ai_search_endpoint"
fi

gh variable set DBAI_CATALOG --repo "$repo" --env "$environment_name" --body "$catalog_name"
gh variable set DATABRICKS_SQL_WAREHOUSE_ID --repo "$repo" --env "$environment_name" --body "$warehouse_id"
gh variable set DBAI_APP_USER --repo "$repo" --env "$environment_name" --body "$app_user"
printf 'Updated GitHub Environment %s: DBAI_CATALOG=%s, DATABRICKS_SQL_WAREHOUSE_ID=%s\n' \
  "$environment_name" "$catalog_name" "$warehouse_id"
printf '\nDatabricks environment configuration complete for %s.\n' "$environment_name"
printf 'Run Deploy Workload, then Bootstrap Environment for this environment.\n'
