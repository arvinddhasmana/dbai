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

```
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
workspaces. The one-time Databricks bootstrap administrator must be an account
administrator and a workspace administrator. Normal deployment workflows use
the lower-privilege Azure OIDC identity.

## 1. Set Stable Environment Values

Replace only the values in angle brackets. These values identify the Azure
subscription, GitHub repository, and Databricks account. They normally remain
the same when a workspace is recreated.

```
export REPO="arvinddhasmana/dbai"
export ENVIRONMENT="dev"

export SUBSCRIPTION_ID="<azure-subscription-id>"
export ACCOUNT_ID="<databricks-account-id>"
export DEPLOYMENT_CLIENT_ID="<entra-application-client-id>"
export AZURE_LOCATION="eastus2"

export RESOURCE_GROUP="rg-dbai-${ENVIRONMENT}"
export MANAGED_RESOURCE_GROUP="rg-dbai-${ENVIRONMENT}-managed"
export WORKSPACE_NAME="dbai-${ENVIRONMENT}"

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

The following values belong to the current Databricks workspace and can change
after recreation. Do not copy them from an old deployment:

- Workspace URL and workspace ID
- Isolated Unity Catalog name
- SQL Warehouse ID
- App service-principal ID
- AI Search endpoint and index IDs

## 2. Sign In to Azure and GitHub

```
az login --use-device-code
az account set --subscription "$SUBSCRIPTION_ID"
gh auth login
```

Confirm the selected Azure subscription:

```
az account show \
  --query '{subscriptionId:id,tenantId:tenantId,name:name}' \
  --output table
```

## 3. Configure GitHub OIDC Once

Run this once when setting up the repository. It creates or reuses the Entra
application, GitHub federated credentials, Azure role assignment, and GitHub
Environments:

```
scripts/local/configure_github_azure.sh \
  --repo "$REPO" \
  --subscription-id "$SUBSCRIPTION_ID"
```

Store the printed client ID as `DEPLOYMENT_CLIENT_ID`. The script configures
`AZURE_CLIENT_ID` and `AZURE_TENANT_ID` secrets plus the Azure subscription,
location, resource group, managed resource group, and workspace name variables
in each selected GitHub Environment. Do not rerun this step after a workspace
recreation unless the OIDC application or federated credentials are missing.

## 4. Deploy Azure Infrastructure

Run **Deploy Infrastructure** from the repository's GitHub Actions tab and
select `$ENVIRONMENT`. This creates the dedicated resource groups and
Databricks workspace. It does not create the Databricks catalog, SQL Warehouse,
App, jobs, or demo data.

## 5. Configure Databricks Environment

An account administrator must create a dedicated Databricks-managed bootstrap
service principal, grant it **account admin** and workspace administrator
access, and create a Databricks OAuth M2M secret with the `all-apis` scope.
Store its client ID and secret as the protected
GitHub Environment secrets `DATABRICKS_ADMIN_CLIENT_ID` and
`DATABRICKS_ADMIN_CLIENT_SECRET`. Store the Databricks account ID as the
protected variable `DATABRICKS_ACCOUNT_ID`. Configure required reviewers on
the environment.

Run **Configure Databricks Environment** from GitHub Actions after **Deploy
Infrastructure**. Select `$ENVIRONMENT`, approve the environment protection,
and optionally provide the App user. The workflow resolves the current
workspace URL and ID, configures account and workspace permissions with OAuth
M2M, discovers or creates the isolated catalog and SQL Warehouse, and writes
the current `DBAI_CATALOG`, `DATABRICKS_SQL_WAREHOUSE_ID`, and `DBAI_APP_USER`
values to that GitHub Environment. It is safe to rerun after workspace
recreation. `DBAI_APP_USER` is an email address for the Databricks OBO user;
the workflow creates the account user when needed, assigns it to the selected
workspace, and waits for the assignment to propagate before applying permissions.
The selected GitHub Environment must also contain a `GH_ADMIN_TOKEN` secret with
the repository permission **Environments: Read and write**.
For a fine-grained token, select `arvinddhasmana` as resource owner and this
repository as the only repository; approve it if required. A classic PAT
requires the `repo` scope. The default `GITHUB_TOKEN` cannot update environment
variables through the GitHub API.

The account ID is distinct from the Azure subscription ID and workspace ID.
The OAuth secret is shown only once when created; do not commit it or print it
in workflow logs.

## 6. Deploy the Workload

The workload deployment updates the Bundle-managed jobs and App resource, and
uploads the full `app/` directory to the Bundle workspace path. It does not
start or deploy the App revision yet because the AI Search index is created by
Bootstrap.

### GitHub Actions

1. Open the repository's **Actions** tab.
2. Select **Deploy Workload**.
3. Select `$ENVIRONMENT`.
4. Run the workflow.

The workload must be deployed before Bootstrap because the Bootstrap workflow
uses `--skip-deploy`. The App is activated and its revision is deployed by
Bootstrap after the data and AI Search objects exist.

## 7. Bootstrap Data and AI Search

Bootstrap creates the demo data, contract chunks, AI Search resources, and
Genie-facing function. It also applies the final App and OBO user permissions.

### GitHub Actions

1. Open the repository's **Actions** tab.
2. Select **Bootstrap Databricks Environment**.
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
  endpoint and triggered Delta Sync index if they are missing. Creating the
  index automatically starts its initial synchronization.
4. Run `sql/01_genie_search.sql` in the configured SQL Warehouse if the Genie
  function was not created by Bootstrap.

The refresh job supports `INGESTION_MODE=full_rebuild`; otherwise it processes
new, updated, and deleted files incrementally. When the triggered index is
created, its initial synchronization starts automatically. Wait for the index
to become Online and the sync to become Completed after Bootstrap.

## 8. Wait for AI Search and Validate

The index uses triggered synchronization. Creating the index during Bootstrap
automatically starts its initial sync. Bootstrap finishes while the index may
still be provisioning or syncing.

In the Databricks workspace:

1. Open the AI Search endpoint.
2. Open the contract index.
3. Wait until the index is **Online** and the sync is **Completed**.

Then run:

```
python3 scripts/local/validate_demo_workspace.py --require-index
```

## 9. Configure MLflow UC Tracing

The App records redacted MLflow AgentServer spans in Unity Catalog. The trace
tables must use an explicit Azure-backed storage location; Databricks default
storage is not supported for MLflow UC trace telemetry.

The active development configuration is:

| Setting | Value |
| --- | --- |
| Experiment | `/Shared/globalmart-supply-chain-agent-uc-v2-dev` |
| Trace location | `globalmart.agent_observability.contract_agent_traces` |
| SQL Warehouse | `a749a7ee30b8f4f4` in the current workspace |
| Storage account | `dbaitrace7405617519` in `eastus2` |
| Filesystem | `mlflow-traces` |
| Access Connector | `dbai-mlflow-traces-connector` |
| UC storage credential | `mlflow-trace-credential` |
| UC external location | `mlflow-traces-globalmart` |

For another environment, replace these development values with unique names
and the target workspace's catalog and warehouse.

### 9.1 Create Azure trace storage

Create an ADLS Gen2 account with hierarchical namespace enabled and private
blob access. The storage account name must be globally unique and contain only
lowercase letters and numbers.

```
export SUBSCRIPTION_ID="<azure-subscription-id>"
export RESOURCE_GROUP="<resource-group>"
export LOCATION="eastus2"
export STORAGE_ACCOUNT="<globally-unique-lowercase-name>"
export TRACE_FILESYSTEM="mlflow-traces"

az storage account create \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --subscription "$SUBSCRIPTION_ID" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --hns \
  --min-tls-version TLS1_2 \
  --https-only true \
  --allow-blob-public-access false

az storage container create \
  --name "$TRACE_FILESYSTEM" \
  --account-name "$STORAGE_ACCOUNT" \
  --auth-mode login \
  --public-access off
```

Create a system-assigned Databricks Access Connector and grant it
**Storage Blob Data Contributor** on the storage account:

```
export ACCESS_CONNECTOR="dbai-mlflow-traces-connector"

az databricks access-connector create \
  --name "$ACCESS_CONNECTOR" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --identity-type SystemAssigned \
  --subscription "$SUBSCRIPTION_ID"

CONNECTOR_PRINCIPAL_ID="$(az databricks access-connector show \
  --name "$ACCESS_CONNECTOR" \
  --resource-group "$RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID" \
  --query identity.principalId --output tsv)"

STORAGE_ID="$(az storage account show \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID" \
  --query id --output tsv)"

az role assignment create \
  --assignee-object-id "$CONNECTOR_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "$STORAGE_ID" \
  --subscription "$SUBSCRIPTION_ID"
```

### 9.2 Register the Unity Catalog storage location

Use a metastore administrator or an identity with the required storage
credential and external-location privileges:

```
export DATABRICKS_CONFIG_PROFILE="<workspace-profile>"
export DATABRICKS_CATALOG="<catalog-name>"
export TRACE_SCHEMA="agent_observability"
export TRACE_CREDENTIAL="mlflow-trace-credential"
export TRACE_LOCATION="mlflow-traces-globalmart"
export TRACE_URL="abfss://${TRACE_FILESYSTEM}@${STORAGE_ACCOUNT}.dfs.core.windows.net/"
export ACCESS_CONNECTOR_ID="$(az databricks access-connector show \
  --name "$ACCESS_CONNECTOR" \
  --resource-group "$RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID" \
  --query id --output tsv)"

databricks storage-credentials create --profile "$DATABRICKS_CONFIG_PROFILE" \
  --json "{\"name\":\"$TRACE_CREDENTIAL\",\"azure_managed_identity\":{\"access_connector_id\":\"$ACCESS_CONNECTOR_ID\"},\"comment\":\"Azure-backed MLflow trace storage\"}"

databricks external-locations create "$TRACE_LOCATION" "$TRACE_URL" "$TRACE_CREDENTIAL" \
  --profile "$DATABRICKS_CONFIG_PROFILE"
```

Create the explicit-storage schema from the SQL Editor or through the SQL
Statement Execution API using the target warehouse:

```sql
CREATE SCHEMA IF NOT EXISTS <catalog-name>.agent_observability
MANAGED LOCATION
'abfss://<filesystem>@<storage-account>.dfs.core.windows.net/';
```

Do not put MLflow trace tables in the application schema
`<catalog-name>.supply_chain` when that schema uses default storage.

### 9.3 Grant trace access

The App service principal and the user who runs trace verification need catalog
and schema access. The repository automation also grants `SELECT` to all
current and future tables in the trace schema and `MODIFY` only to the writable
OTel tables:

```
export DBAI_CATALOG="<catalog-name>"
export DATABRICKS_SQL_WAREHOUSE_ID="<warehouse-id>"
export DBAI_APP_USER="<databricks-user-email>"

uv run python scripts/local/grant_data_access.py \
  --app-name "dbai-supply-agent-dev" \
  --catalog "$DBAI_CATALOG" \
  --warehouse-id "$DATABRICKS_SQL_WAREHOUSE_ID" \
  --user-principal "$DBAI_APP_USER"
```

The equivalent SQL for a verification user is:

```sql
GRANT USE CATALOG ON CATALOG <catalog-name>
TO `<databricks-user-email>`;
GRANT USE SCHEMA ON SCHEMA <catalog-name>.agent_observability
TO `<databricks-user-email>`;
GRANT SELECT ON ALL TABLES IN SCHEMA <catalog-name>.agent_observability
TO `<databricks-user-email>`;
```

`GRANT SELECT ON SCHEMA` is not the schema-wide table grant. Use
`GRANT SELECT ON ALL TABLES IN SCHEMA` as shown above.

### 9.4 Configure and deploy the App

The App must receive both warehouse variables. `DATABRICKS_SQL_WAREHOUSE_ID`
is used by the retrieval code; `MLFLOW_TRACING_SQL_WAREHOUSE_ID` is required
when MLflow searches UC trace tables.

```
export DATABRICKS_CONFIG_PROFILE="<workspace-profile>"
export DBAI_CLI_BIN="/tmp/databricks-cli-1.15.0/databricks"
export DBAI_CATALOG="<catalog-name>"
export DATABRICKS_SQL_WAREHOUSE_ID="<warehouse-id>"
export MLFLOW_EXPERIMENT_NAME="/Shared/globalmart-supply-chain-agent-uc-v2-dev"
export MLFLOW_TRACE_CATALOG="$DBAI_CATALOG"
export MLFLOW_TRACE_SCHEMA="agent_observability"
export MLFLOW_TRACE_TABLE_PREFIX="contract_agent_traces"

scripts/local/deploy_app.sh
```

The existing populated experiment cannot be relinked to a new UC trace
destination. Use a new experiment, such as the `uc-v2` experiment above, and
keep the historical default-storage experiment unchanged.

### 9.5 Verify the App and traces

Open the deployed App in a browser and sign in with a user who has SQL
Warehouse and Unity Catalog permissions. Ask a question such as:

```text
What are the weather-delay rules for VEND-789?
```

An unauthenticated `curl` to the App returns HTTP `302` because Databricks
redirects the request to sign-in before it reaches `/invocations`. Use the
browser or an authenticated request for end-to-end testing.

Then verify traces locally from the agent directory:

```
cd agents/supply_chain_agent
export MLFLOW_TRACKING_URI=databricks
export MLFLOW_EXPERIMENT_NAME=/Shared/globalmart-supply-chain-agent-uc-v2-dev
export MLFLOW_TRACING_SQL_WAREHOUSE_ID="$DATABRICKS_SQL_WAREHOUSE_ID"

PYTHONPATH=src uv run python -m evaluation.verify_traces \
  --experiment "$MLFLOW_EXPERIMENT_NAME" \
  --trace-location globalmart.agent_observability.contract_agent_traces
```

Successful output includes a positive `trace_count` and the span names
`contract_agent.invoke` and `contract_search`. The no-time-range warning is
non-fatal; add a time filter when querying large trace tables.

In Databricks SQL Editor, select the same warehouse and inspect the trace data:

```sql
SHOW TABLES IN <catalog-name>.agent_observability;

SELECT COUNT(*)
FROM <catalog-name>.agent_observability.contract_agent_traces_otel_spans;

SELECT *
FROM <catalog-name>.agent_observability.contract_agent_traces_otel_spans
ORDER BY timestamp_ms DESC
LIMIT 20;
```

## 10. Configure Genie

Genie is the SQL-first conversational experience for structured inventory and
vendor analysis, with optional contract retrieval through the
`search_vendor_contracts` table-valued function. Configure it only after
Bootstrap has created the function and the triggered AI Search index has
completed its initial synchronization.

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

```
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

## 11. Use the Mosaic AI Agent App

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

```
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

```
scripts/local/destroy_demo_environment.sh --yes
```

The teardown deletes only the deployment-owned primary and Databricks managed
resource groups. Azure platform resource groups, including `NetworkWatcherRG`,
are intentionally preserved because they may be shared by other resources in
the subscription.

If the resource group was already deleted manually, delete the deployment-owned
managed resource group only after confirming that it belongs to this
environment:

```
az group delete \
  --name "$MANAGED_RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID" \
  --yes
```

Wait until deletion has completed before recreating the same workspace names.
You can check with:

```
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

```
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

```
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
