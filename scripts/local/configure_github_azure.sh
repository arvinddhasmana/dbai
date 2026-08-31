#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPOSITORY_ROOT"

usage() {
  cat <<'EOF'
Usage: scripts/local/configure_github_azure.sh [options]

Configure GitHub Environments and Azure OIDC access for the deployment workflows.
The administrator must already be authenticated with `az login` and `gh auth login`.

Options:
  --repo OWNER/REPO       GitHub repository (default: current repository)
  --subscription-id ID    Azure subscription (default: current Azure subscription)
  --app-name NAME         Entra application display name (default: dbai-github-actions)
  --environments LIST     Comma-separated environments (default: dev,test,prod)
  --role NAME             Azure role at subscription scope (default: Contributor)
  --help                  Show this help

Optional per-environment variables are read from the shell:
  DBAI_CATALOG_DEV, DATABRICKS_SQL_WAREHOUSE_ID_DEV
  DBAI_CATALOG_TEST, DATABRICKS_SQL_WAREHOUSE_ID_TEST
  DBAI_CATALOG_PROD, DATABRICKS_SQL_WAREHOUSE_ID_PROD
EOF
}

repo=""
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
app_name="${DBAI_AZURE_APP_NAME:-dbai-github-actions}"
environments_csv="${DBAI_ENVIRONMENTS:-dev,test,prod}"
role_name="${AZURE_ROLE_NAME:-Contributor}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo="$2"
      shift 2
      ;;
    --subscription-id)
      subscription_id="$2"
      shift 2
      ;;
    --app-name)
      app_name="$2"
      shift 2
      ;;
    --environments)
      environments_csv="$2"
      shift 2
      ;;
    --role)
      role_name="$2"
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

for command in az gh; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Required command is missing: %s\n' "$command" >&2
    exit 1
  fi
done

az account show --only-show-errors >/dev/null
gh auth status >/dev/null

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

tenant_id="$(az account show --query tenantId --output tsv)"
subscription_scope="/subscriptions/${subscription_id}"
issuer="https://token.actions.githubusercontent.com"
audience="api://AzureADTokenExchange"

app_id="${DBAI_AZURE_CLIENT_ID:-}"
if [[ -z "$app_id" ]]; then
  app_id="$(az ad app list --display-name "$app_name" --query '[0].appId' --output tsv)"
fi
if [[ -z "$app_id" ]]; then
  app_id="$(az ad app create --display-name "$app_name" --query appId --output tsv)"
  printf 'Created Entra application: %s\n' "$app_id"
else
  printf 'Using Entra application: %s\n' "$app_id"
fi

service_principal_object_id="$(az ad sp list --filter "appId eq '$app_id'" --query '[0].id' --output tsv)"
if [[ -z "$service_principal_object_id" ]]; then
  service_principal_object_id="$(az ad sp create --id "$app_id" --query id --output tsv)"
  printf 'Created service principal: %s\n' "$service_principal_object_id"
else
  printf 'Using service principal: %s\n' "$service_principal_object_id"
fi

if [[ -z "$(az role assignment list \
  --assignee-object-id "$service_principal_object_id" \
  --scope "$subscription_scope" \
  --role "$role_name" \
  --query '[0].id' \
  --output tsv)" ]]; then
  az role assignment create \
    --assignee-object-id "$service_principal_object_id" \
    --assignee-principal-type ServicePrincipal \
    --role "$role_name" \
    --scope "$subscription_scope" \
    --output none
  printf 'Assigned %s at %s\n' "$role_name" "$subscription_scope"
else
  printf 'Role assignment already exists: %s at %s\n' "$role_name" "$subscription_scope"
fi

IFS=',' read -r -a environments <<< "$environments_csv"
for environment in "${environments[@]}"; do
  environment="${environment//[[:space:]]/}"
  if [[ ! "$environment" =~ ^[a-z0-9-]+$ ]]; then
    printf 'Environment names may contain lowercase letters, numbers, and hyphens: %s\n' "$environment" >&2
    exit 1
  fi

  credential_name="github-${environment}"
  subject="repo:${repo}:environment:${environment}"
  existing_subject="$(az ad app federated-credential list \
    --id "$app_id" \
    --query "[?name=='${credential_name}'].subject | [0]" \
    --output tsv)"
  if [[ -z "$existing_subject" ]]; then
    credential_json="$(printf '{"name":"%s","issuer":"%s","subject":"%s","description":"GitHub Actions %s deployment","audiences":["%s"]}' \
      "$credential_name" "$issuer" "$subject" "$environment" "$audience")"
    az ad app federated-credential create \
      --id "$app_id" \
      --parameters "$credential_json" \
      --output none
    printf 'Created federated credential: %s\n' "$credential_name"
  elif [[ "$existing_subject" != "$subject" ]]; then
    printf 'Federated credential %s exists with a different subject. Remove or update it before rerunning.\n' "$credential_name" >&2
    exit 1
  else
    printf 'Federated credential already exists: %s\n' "$credential_name"
  fi

  gh api --method PUT "repos/${repo}/environments/${environment}" \
    --field wait_timer=0 \
    --silent

  gh secret set AZURE_CLIENT_ID --repo "$repo" --env "$environment" --body "$app_id"
  gh secret set AZURE_TENANT_ID --repo "$repo" --env "$environment" --body "$tenant_id"
  gh variable set AZURE_SUBSCRIPTION_ID --repo "$repo" --env "$environment" --body "$subscription_id"
  gh variable set AZURE_LOCATION --repo "$repo" --env "$environment" --body "${AZURE_LOCATION:-eastus2}"
  gh variable set DBAI_RESOURCE_GROUP --repo "$repo" --env "$environment" --body "${DBAI_RESOURCE_GROUP:-rg-dbai-${environment}}"
  gh variable set DBAI_MANAGED_RESOURCE_GROUP --repo "$repo" --env "$environment" --body "${DBAI_MANAGED_RESOURCE_GROUP:-rg-dbai-${environment}-managed}"
  gh variable set DBAI_WORKSPACE_NAME --repo "$repo" --env "$environment" --body "${DBAI_WORKSPACE_NAME:-dbai-${environment}}"

  catalog_variable="DBAI_CATALOG_${environment^^}"
  warehouse_variable="DATABRICKS_SQL_WAREHOUSE_ID_${environment^^}"
  catalog_value="${!catalog_variable:-}"
  warehouse_value="${!warehouse_variable:-}"
  if [[ -n "$catalog_value" ]]; then
    gh variable set DBAI_CATALOG --repo "$repo" --env "$environment" --body "$catalog_value"
  fi
  if [[ -n "$warehouse_value" ]]; then
    gh variable set DATABRICKS_SQL_WAREHOUSE_ID --repo "$repo" --env "$environment" --body "$warehouse_value"
  fi

  printf 'Configured GitHub Environment: %s\n' "$environment"
done

printf '\nConfiguration complete for %s.\n' "$repo"
printf 'Client ID: %s\n' "$app_id"
printf 'Tenant ID: %s\n' "$tenant_id"
printf 'Set DBAI_CATALOG_<ENV> and DATABRICKS_SQL_WAREHOUSE_ID_<ENV> before workload/bootstrap runs if they were not supplied.\n'
