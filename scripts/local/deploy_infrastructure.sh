#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPOSITORY_ROOT"

subscription_id="${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID before deploying infrastructure.}"
location="${AZURE_LOCATION:-eastus2}"
environment_name="${DBAI_ENVIRONMENT:-dev}"
resource_group="${DBAI_RESOURCE_GROUP:-rg-dbai-${environment_name}}"
managed_resource_group="${DBAI_MANAGED_RESOURCE_GROUP:-rg-dbai-${environment_name}-managed}"
workspace_name="${DBAI_WORKSPACE_NAME:-dbai-${environment_name}}"

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
               workspaceName="$workspace_name" \
               location="$location" \
  --subscription "$subscription_id" \
  --output none

workspace_url="$(az databricks workspace show \
  --resource-group "$resource_group" \
  --name "$workspace_name" \
  --subscription "$subscription_id" \
  --query workspaceUrl \
  --output tsv)"

if [[ -z "$workspace_url" ]]; then
  printf '%s\n' "Azure Databricks workspace URL was not returned." >&2
  exit 1
fi

printf 'Infrastructure ready. Workspace: %s\n' "$workspace_url"
