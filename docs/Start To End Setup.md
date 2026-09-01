# Start-to-End Setup

This is the complete setup for one disposable environment. It covers the first
installation and the recovery sequence after the Azure resource group or
Databricks workspace has been deleted and recreated.

The process has three separate lifecycles:

1. **Administration**: configure Azure, GitHub OIDC, and Databricks access.
2. **Infrastructure**: create the Azure resource groups and Databricks workspace.
3. **Workload and data**: deploy the App and jobs, then load demo data and create
   the search resources.

The administration step is normally performed once. Infrastructure and workload
steps can be safely repeated for the selected environment.

## Before You Start

Run these commands from the repository root:

```bash
cd /home/arvind/workspace/dbai

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

The following commands must be available:

- `az` with the Azure Databricks extension
- `gh`
- `databricks`
- `jq`
- `python3`

The Azure account must be allowed to create resource groups and Databricks
workspaces. The Databricks administrator must be an account administrator and a
workspace administrator.

## 1. Set Stable Environment Values

Replace only the values in angle brackets. These values identify the Azure
subscription, GitHub repository, and Databricks account. They normally remain
the same when a workspace is recreated.

```bash
export REPO="arvinddhasmana/dbai"
export ENVIRONMENT="dev"

export SUBSCRIPTION_ID="<azure-subscription-id>"
export ACCOUNT_ID="<databricks-account-id>"
export DEPLOYMENT_CLIENT_ID="<entra-application-client-id>"
export AZURE_LOCATION="eastus2"

export RESOURCE_GROUP="rg-dbai-${ENVIRONMENT}"
export MANAGED_RESOURCE_GROUP="rg-dbai-${ENVIRONMENT}-managed"
export WORKSPACE_NAME="dbai-${ENVIRONMENT}"

export ACCOUNT_PROFILE="account-admin"
export WORKSPACE_PROFILE="dbai-${ENVIRONMENT}-admin"
export BUNDLE_TARGET="${ENVIRONMENT}"
export STATE_DIR=".dbai-state"
```

### Where to find the values

| Value | Where to find it |
| --- | --- |
| `SUBSCRIPTION_ID` | Azure Portal > **Subscriptions**, or `az account list` |
| `ACCOUNT_ID` | Databricks account console > **Settings > Account ID** |
| `DEPLOYMENT_CLIENT_ID` | The client ID printed by the GitHub/Azure setup command, or the Entra app registration used for GitHub OIDC |
| `RESOURCE_GROUP` | Azure Portal > **Resource groups**, or the GitHub Environment variable `DBAI_RESOURCE_GROUP` |
| `WORKSPACE_NAME` | Azure Portal > **Databricks workspaces**, or the GitHub Environment variable `DBAI_WORKSPACE_NAME` |
| `ACCOUNT_PROFILE` | Any local name used for the Databricks account-admin CLI profile |
| `WORKSPACE_PROFILE` | Any local name used for the Databricks workspace-admin CLI profile |

The following values belong to the current Databricks workspace and can change
after recreation. Do not copy them from an old deployment:

- Workspace URL and workspace ID
- Isolated Unity Catalog name
- SQL Warehouse ID
- App service-principal ID
- AI Search endpoint and index IDs

## 2. Sign In to Azure and GitHub

```bash
az login --use-device-code
az account set --subscription "$SUBSCRIPTION_ID"
gh auth login
```

Confirm the selected Azure subscription:

```bash
az account show \
  --query '{subscriptionId:id,tenantId:tenantId,name:name}' \
  --output table
```

## 3. Configure GitHub OIDC Once

Run this once when setting up the repository. It creates or reuses the Entra
application, GitHub federated credentials, Azure role assignment, and GitHub
Environments.

```bash
scripts/local/configure_github_azure.sh \
  --repo "$REPO" \
  --subscription-id "$SUBSCRIPTION_ID"
```

The script prints the Entra application client ID. Store it in the current
terminal for later steps:

```bash
export DEPLOYMENT_CLIENT_ID="<client-id-printed-by-the-script>"
```

When rerunning this step, pass the existing client ID explicitly if multiple
Entra applications use the display name `dbai-github-actions`:

```bash
scripts/local/configure_github_azure.sh \
  --repo "$REPO" \
  --subscription-id "$SUBSCRIPTION_ID" \
  --app-name dbai-github-actions \
  --client-id "$DEPLOYMENT_CLIENT_ID" \
  --environments "$ENVIRONMENT"
```

You do not need to run this step again just because the Azure resource group or
Databricks workspace was recreated. Run it again only when the repository's
GitHub OIDC configuration or Entra application does not exist.

The script uses these default Azure resource names unless the corresponding
GitHub Environment variables already contain custom names:

- `rg-dbai-dev`, `rg-dbai-test`, or `rg-dbai-prod`
- `rg-dbai-<environment>-managed`
- `dbai-<environment>`

Custom resource-group names must remain under the selected environment's
`rg-dbai-<environment>` namespace. Do not point these variables at an existing
shared or platform-managed resource group.

## 4. Deploy Azure Infrastructure

Set the infrastructure values used by the local script:

```bash
export AZURE_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"
export AZURE_LOCATION="$AZURE_LOCATION"
export DBAI_ENVIRONMENT="$ENVIRONMENT"
export DBAI_RESOURCE_GROUP="$RESOURCE_GROUP"
export DBAI_MANAGED_RESOURCE_GROUP="$MANAGED_RESOURCE_GROUP"
export DBAI_WORKSPACE_NAME="$WORKSPACE_NAME"
```

Deploy the subscription-scoped Bicep template:

```bash
scripts/local/deploy_infrastructure.sh
```

Alternatively, run the **Deploy Infrastructure** workflow from the GitHub
Actions tab and select `$ENVIRONMENT`.

This step creates the dedicated resource group and the Azure Databricks
workspace. It does not create the Databricks catalog, SQL Warehouse, App, jobs,
or demo data.

### Optional one-command local deployment

For a disposable local Azure demo, the convenience orchestrator combines
infrastructure, workload deployment, bootstrap, and App activation:

```bash
export AZURE_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"
export AZURE_LOCATION="$AZURE_LOCATION"
scripts/local/deploy_demo_environment.sh
```

It discovers the workspace-specific isolated catalog, creates a single-node
serverless SQL Warehouse when one is not supplied, and records deployment state
under `.dbai-state/`. Use the staged steps in this guide when infrastructure,
workload, and bootstrap need to remain separate, as they do in GitHub Actions.

## 5. Resolve the New Workspace URL

Always retrieve the URL after infrastructure deployment. It may be different
from the URL of a deleted workspace.

```bash
export WORKSPACE_URL="$(az databricks workspace show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WORKSPACE_NAME" \
  --subscription "$SUBSCRIPTION_ID" \
  --query workspaceUrl \
  --output tsv)"
```

Check the result:

```bash
printf 'Databricks workspace: https://%s\n' "${WORKSPACE_URL#https://}"
```

The same value is available in Azure Portal by opening the Databricks workspace
and selecting its workspace URL.

## 6. Authenticate Databricks CLI Profiles

Authenticate the account-admin profile:

```bash
databricks auth login \
  --profile "$ACCOUNT_PROFILE" \
  --host https://accounts.azuredatabricks.net \
  --account-id "$ACCOUNT_ID" \
  --skip-workspace
```

Authenticate the workspace-admin profile against the new workspace:

```bash
databricks auth login \
  --profile "$WORKSPACE_PROFILE" \
  --host "https://${WORKSPACE_URL#https://}"
```
If the workspace was deleted and recreated, then the workspace-admin profile may still point to the old workspace. If you encounter host conflict error then use following command to explicitly point profile to the new workspace:

```bash
# 1. Initiate login directly with the host
databricks auth login --host https://${WORKSPACE_URL#https://}

# 2. When prompted by the CLI terminal, type: dbai-dev-admin or whatever you set in WORKSPACE_PROFILE variable
```

Verify that the profile points to the new workspace:

```bash
databricks auth describe --profile $WORKSPACE_PROFILE
```

If an existing profile still points to the deleted workspace, the second login
replaces its host and credentials.

### Optional VS Code OAuth profile

Complete this once on the machine where VS Code and the Databricks extension
run:

1. Install or enable the **Databricks** extension for Visual Studio Code.
2. Open this repository in VS Code and open the Databricks **Configuration** view.
3. Select **Auth Type**, then the gear icon for **Sign in to Databricks workspace**.
4. Choose **OAuth (user to machine)** in the Command Palette.
5. Create or select a local profile such as `aiarchitect` and complete browser sign-in.
6. Confirm that the Configuration view shows the current workspace and OAuth authentication.

The extension stores the OAuth profile for the Databricks CLI and SDKs. Do not
commit credentials or generated `.databricks/` state. To verify the profile in
a terminal:

```bash
export DATABRICKS_CONFIG_PROFILE="$WORKSPACE_PROFILE"
export DATABRICKS_SQL_WAREHOUSE_ID="<serverless-sql-warehouse-id>"
databricks auth describe --profile "$DATABRICKS_CONFIG_PROFILE"
databricks current-user me --profile "$DATABRICKS_CONFIG_PROFILE"
```

For local App development, create the ignored root dotenv file when the
repository provides `.env.example`:

```bash
cp .env.example .env
# Edit .env and replace the placeholder warehouse ID.
```

The App loads this file when started from the repository. Authentication
remains managed by the Databricks OAuth profile; do not put tokens in `.env`.

## 7. Configure the Databricks Environment

Run this as a Databricks account administrator and workspace administrator:

```bash
scripts/local/configure_databricks_environment.sh \
  --environment "$ENVIRONMENT" \
  --repo "$REPO" \
  --subscription-id "$SUBSCRIPTION_ID" \
  --deployment-client-id "$DEPLOYMENT_CLIENT_ID" \
  --account-profile "$ACCOUNT_PROFILE" \
  --workspace-profile "$WORKSPACE_PROFILE"
```

If the App will run SQL on behalf of a different user, add this argument to the
same command:

```text
--app-user "<databricks-user-email>"
```

The script is safe to rerun. It discovers or creates the current workspace's
isolated catalog and SQL Warehouse, assigns the deployment service principal,
grants it delegated `MANAGE` on the isolated catalog so Bootstrap can grant
final App and OBO access, sets required workspace permissions, and writes the
current catalog and warehouse values to the selected GitHub Environment.

## 8. Load Current Workspace Values

The Databricks configuration script updates GitHub Environment variables. Load
the new values into the current local terminal before deploying locally:

```bash
export DBAI_CATALOG="$(gh variable get DBAI_CATALOG --repo "$REPO" --env "$ENVIRONMENT")"
export DATABRICKS_SQL_WAREHOUSE_ID="$(gh variable get DATABRICKS_SQL_WAREHOUSE_ID --repo "$REPO" --env "$ENVIRONMENT")"
export DBAI_APP_USER="$(gh variable get DBAI_APP_USER --repo "$REPO" --env "$ENVIRONMENT")"

export DATABRICKS_HOST="https://${WORKSPACE_URL#https://}"
export DATABRICKS_CONFIG_PROFILE="$WORKSPACE_PROFILE"
```

Verify the values:

```bash
printf 'Catalog: %s\n' "$DBAI_CATALOG"
printf 'SQL Warehouse: %s\n' "$DATABRICKS_SQL_WAREHOUSE_ID"
printf 'App user: %s\n' "$DBAI_APP_USER"
```

The catalog and warehouse are also visible in the Databricks workspace UI. The
GitHub Environment is the preferred source for the values used by GitHub
Actions.

## 9. Clear Stale Local State

Run this section after a destroy/recreate operation, or whenever a Bundle error
mentions resources from an old workspace. Moving state to `/tmp` preserves it
for troubleshooting without allowing it to control the new deployment.

Clear values inherited from an old terminal, `.env` file, or shell profile:

```bash
unset DBAI_CATALOG
unset DATABRICKS_SQL_WAREHOUSE_ID
unset DATABRICKS_HOST
unset DATABRICKS_BUNDLE_VAR_catalog
unset DATABRICKS_BUNDLE_VAR_sql_warehouse_id
```

Move the target's local Bundle state aside:

```bash
if [[ -d ".databricks/bundle/${BUNDLE_TARGET}" ]]; then
  mv ".databricks/bundle/${BUNDLE_TARGET}" \
    "/tmp/dbai-bundle-${BUNDLE_TARGET}-$(date +%Y%m%d%H%M%S)"
fi
```

Move cached catalog and warehouse discovery values aside:

```bash
if [[ -d "$STATE_DIR" ]]; then
  state_backup="/tmp/dbai-state-${ENVIRONMENT}-$(date +%Y%m%d%H%M%S)"
  mkdir -p "$state_backup"
  for state_file in \
    "${ENVIRONMENT}-catalog" \
    "${ENVIRONMENT}-sql-warehouse-id"; do
    if [[ -f "$STATE_DIR/$state_file" ]]; then
      mv "$STATE_DIR/$state_file" "$state_backup/"
    fi
  done
fi
```

Re-export the current values after cleanup:

```bash
export DBAI_CATALOG="$(gh variable get DBAI_CATALOG --repo "$REPO" --env "$ENVIRONMENT")"
export DATABRICKS_SQL_WAREHOUSE_ID="$(gh variable get DATABRICKS_SQL_WAREHOUSE_ID --repo "$REPO" --env "$ENVIRONMENT")"
export DBAI_APP_USER="$(gh variable get DBAI_APP_USER --repo "$REPO" --env "$ENVIRONMENT")"
export DATABRICKS_HOST="https://${WORKSPACE_URL#https://}"
export DATABRICKS_CONFIG_PROFILE="$WORKSPACE_PROFILE"
```

Do not delete the entire `~/.databrickscfg` file. It may contain profiles for
other workspaces. Reauthenticate only the workspace profile when its host is
stale.

After a disposable environment is destroyed, remove only its dedicated local
profiles and caches with:

```bash
scripts/local/cleanup_local_databricks.sh \
  --environment "$ENVIRONMENT" \
  --target "$BUNDLE_TARGET" \
  --yes
```

This deletes only `dbai-$ENVIRONMENT` and `dbai-$ENVIRONMENT-admin` profiles,
archives that environment's Bundle and discovery state under `/tmp`, and
preserves the main Databricks config file and unrelated profiles. The teardown
wrapper can perform the same cleanup after Azure deletion with
`--cleanup-local-config`.

## 10. Deploy the Workload

The workload deployment updates the Bundle-managed jobs and App resource, and
uploads the full `app/` directory to the Bundle workspace path. It does not
start or deploy the App revision yet because the AI Search index is created by
Bootstrap.

### Local

```bash
export DBAI_ENVIRONMENT="$ENVIRONMENT"
export DBAI_BUNDLE_TARGET="$BUNDLE_TARGET"
export DBAI_RESOURCE_GROUP="$RESOURCE_GROUP"
export DBAI_WORKSPACE_NAME="$WORKSPACE_NAME"
export AZURE_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"

scripts/local/deploy_workload.sh
```

### GitHub Actions

1. Open the repository's **Actions** tab.
2. Select **Deploy Workload**.
3. Select `$ENVIRONMENT`.
4. Run the workflow.

The workload must be deployed before Bootstrap because the Bootstrap workflow
uses `--skip-deploy`. The App is activated and its revision is deployed by
Bootstrap after the data and AI Search objects exist.

## 11. Bootstrap Data and AI Search

Bootstrap creates the demo data, contract chunks, AI Search resources, and
Genie-facing function. It also applies the final App and OBO user permissions.

### Local

```bash
python3 scripts/local/bootstrap_demo_environment.py \
  --target "$BUNDLE_TARGET" \
  --warehouse-id "$DATABRICKS_SQL_WAREHOUSE_ID" \
  --skip-deploy \
  --app-name "dbai-${ENVIRONMENT}-supply-chain-agent" \
  --user-principal "$DBAI_APP_USER"
scripts/local/deploy_app.sh
```

### GitHub Actions

1. Open the repository's **Actions** tab.
2. Select **Bootstrap Environment**.
3. Select `$ENVIRONMENT`.
4. Run the workflow.

Bootstrap is idempotent for the demo data. It can be rerun after a failed job or
when sample contracts change. Existing ingestion tables are preflighted with
the required `SELECT` and `MODIFY` permissions for the job identity. At the end
of the bootstrap step, `scripts/local/deploy_app.sh` starts the App and deploys
the current revision.

### Manual component setup

Bootstrap performs the following operations automatically. Use these individual
steps only when recovering or inspecting one component:

1. Create `<catalog>.<schema>.vendor_contracts` as a managed Volume and upload
  supported `.txt`, `.md`, `.csv`, `.json`, `.html`, or `.pdf` files.
2. Run `databricks bundle run generate_mock_data -t "$BUNDLE_TARGET"` and
  `databricks bundle run refresh_vendor_contract_chunks -t "$BUNDLE_TARGET"`.
3. Run `scripts/local/create_vendor_contract_index.py` to provision the
  endpoint and triggered Delta Sync index if they are missing.
4. Run `sql/01_genie_search.sql` in the configured SQL Warehouse if the Genie
  function was not created by Bootstrap.

The refresh job supports `INGESTION_MODE=full_rebuild`; otherwise it processes
new, updated, and deleted files incrementally. Trigger AI Search synchronization
after the refresh job completes.

## 12. Synchronize and Validate AI Search

The index uses triggered synchronization. Bootstrap does not automatically
start the sync.

In the Databricks workspace:

1. Open the AI Search endpoint.
2. Open the contract index.
3. Trigger synchronization.
4. Wait until the index is **Online** and the sync is **Completed**.

Then run:

```bash
python3 scripts/local/validate_demo_workspace.py --require-index
```

## 13. Configure Genie

Genie is the SQL-first conversational experience for structured inventory and
vendor analysis, with optional contract retrieval through the
`search_vendor_contracts` table-valued function. Configure it only after
Bootstrap has created the function and the triggered AI Search index has
completed synchronization.

### Create the Genie space

Use the configured serverless SQL Warehouse and add these objects to the Genie
space:

- `<catalog>.<schema>.dim_products`
- `<catalog>.<schema>.dim_vendors`
- `<catalog>.<schema>.fact_inventory_status`
- `<catalog>.<schema>.vendor_contract_chunks_index_rebuilt`
- `<catalog>.<schema>.search_vendor_contracts`

The last two objects are needed for contract questions. The function is created
by `sql/01_genie_search.sql`, which Bootstrap runs automatically. If needed,
execute that SQL file manually in the SQL editor after replacing the catalog
name as appropriate.

Use these instructions in the Genie space:

```text
Use SQL against dim_products, dim_vendors, and fact_inventory_status for exact
numeric answers, joins, totals, dates, inventory values, and account-manager
questions. Use search_vendor_contracts for vendor-contract language, penalties,
weather exceptions, service levels, liability, and delivery obligations.

For hybrid questions, use SQL for structured facts and
search_vendor_contracts for contract evidence, and separate those results.
Pass vendor_id, support_tier, and region when the question provides them.
Always cite source_file and summarize retrieved chunk evidence.

The Search index contains current active Gold chunks only. Bronze event history
and Silver lifecycle records are audit data, not searchable contract content.
When no rows are returned after deletion, say no active searchable contract is
available and do not answer from deleted contract history.
```

### Demonstrate Genie capabilities

Run the read-only checks after the index is synchronized:

```bash
databricks bundle run refresh_vendor_contract_chunks -t "$BUNDLE_TARGET"
python3 scripts/local/validate_demo_workspace.py --require-index
```

Then demonstrate these capabilities in the Genie space:

1. **Structured Text-to-SQL:** Ask, "What is the value of delayed inventory
  associated with Sarah Jenkins?" The expected result is `$12,500.00`.
2. **Contract retrieval:** Ask, "What happens when a Gold Tier Midwest shipment
  is delayed by severe winter weather?" Genie should identify Force Majeure,
  waived daily late penalties, temporary transit liability, and inventory
  holding-cost coverage from the retrieved contract evidence.
3. **Filtered retrieval:** Ask, "What is the delay penalty for Alpine Apparel
  Ltd?" or provide `vendor_id`, `support_tier`, and `region` in the question
  to demonstrate metadata filtering.
4. **Hybrid analysis:** Ask which vendor has delayed inventory and what the
  applicable contract says about delay penalties. Genie should use SQL for the
  inventory fact and `search_vendor_contracts` for contract evidence, keeping
  the two results clearly separated.

For the repeatable add, update, and delete lifecycle, including expected Genie
answers after each index synchronization, follow the
[Contract Change Demo Runbook](05-contract-change-demo.md). The read-only
function checks are in `sql/02_genie_smoke_tests.sql`.

## 14. Use the Mosaic AI Agent App

Open the deployed `supply_chain_agent` Databricks App and ask structured,
contract, or mixed questions. The App uses an MLflow AgentServer, a Databricks
model-serving endpoint, governed SQL lookups, and AI Search retrieval over the
same data foundation as Genie.

Example questions:

- "Which inventory is delayed and what is its value?"
- "What are the weather-delay rules for VEND-789?"
- "Which vendor supplies the Thermal Winter Coats, and what is their current
  transit status?"

The App uses the signed-in user's authorization for SQL. Users need `CAN USE`
on the SQL Warehouse and the required Unity Catalog privileges on the Gold
tables, the search index table, and `search_vendor_contracts`. Contract answers
include source-file and chunk citations when evidence is available.

## Destroy and Recreate

Use this only for disposable environments. Confirm that both resource groups
belong exclusively to this environment before deleting them.

### Check the resource groups

```bash
az group show \
  --name "$RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID" \
  --query '{name:name,location:location,provisioningState:properties.provisioningState}' \
  --output table

az group show \
  --name "$MANAGED_RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID" \
  --query '{name:name,location:location,provisioningState:properties.provisioningState}' \
  --output table
```

### Delete the environment

The repository teardown script also removes Databricks data-plane objects before
Azure deletion. Use it when the old workspace is still reachable:

```bash
scripts/local/destroy_demo_environment.sh --yes
```

If the resource group was already deleted manually, delete the deployment-owned
managed resource group only after confirming that it belongs to this
environment:

```bash
az group delete \
  --name "$MANAGED_RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID" \
  --yes
```

Wait until deletion has completed before recreating the same workspace names.
You can check with:

```bash
az group show \
  --name "$RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID"
```

A not-found response means the resource group is gone. Repeat for
`$MANAGED_RESOURCE_GROUP`.

### Recreate the environment

Start at [Step 4](#4-deploy-azure-infrastructure) and then complete every later
step, especially these recovery steps:

1. Resolve the new workspace URL.
2. Reauthenticate the workspace CLI profile.
3. Run the Databricks environment configuration script.
4. Load the new catalog and SQL Warehouse ID.
5. Clear local Bundle and `.dbai-state` files.
6. Deploy the workload.
7. Bootstrap data.
8. Synchronize and validate AI Search.

The Azure subscription ID, Databricks account ID, GitHub repository, and GitHub
OIDC client ID normally remain unchanged. Workspace URL, workspace ID, catalog,
warehouse ID, App identity, and search resource IDs must be rediscovered.

## Troubleshooting

### `Could not resolve the deployed workspace`

Check the resource group and workspace name:

```bash
az databricks workspace list \
  --subscription "$SUBSCRIPTION_ID" \
  --query '[].{name:name,resourceGroup:resourceGroup,workspaceUrl:workspaceUrl}' \
  --output table
```

### Bundle errors mention an old workspace

Run [Step 9](#9-clear-stale-local-state), then authenticate the workspace
profile again and rerun workload deployment.

### `DBAI_CATALOG` or warehouse ID is wrong

Read the values from the selected GitHub Environment:

```bash
gh variable get DBAI_CATALOG --repo "$REPO" --env "$ENVIRONMENT"
gh variable get DATABRICKS_SQL_WAREHOUSE_ID --repo "$REPO" --env "$ENVIRONMENT"
```

Do not reuse values from the deleted workspace.

### Bootstrap reports missing `MODIFY` or `SELECT`

Rerun the Databricks environment configuration step and then rerun Bootstrap.
The bootstrap preflight grants the required permissions on existing ingestion
tables to the identity running the Bundle jobs.

### Contract retrieval returns no results

Confirm that the contract refresh completed, the source table contains rows,
and the triggered AI Search synchronization completed successfully.
```
