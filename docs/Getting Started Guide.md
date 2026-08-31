
## End-to-end process

This repository contains a disposable Databricks demo environment. The
bootstrap script creates the catalog objects, bundle jobs, contract data, AI
Search endpoint and index, and Genie function. It does not trigger the
`TRIGGERED` AI Search index; synchronization remains a deliberate manual step.

### Authentication

The deployment script discovers the workspace-specific isolated Unity Catalog
created by Azure Databricks and stores its name in `.dbai-state/`. Set
`DBAI_CATALOG` only when you need to override that catalog.

For browserless deployment, authenticate Azure CLI first and select its unified
authentication mode:

```bash
az login
export DBAI_AUTH_MODE=azure-cli
scripts/local/deploy_demo_environment.sh
```

Databricks OAuth machine-to-machine authentication is also supported:

```bash
export DBAI_AUTH_MODE=oauth-m2m
export DATABRICKS_CLIENT_ID=<client-id>
export DATABRICKS_CLIENT_SECRET=<client-secret>
scripts/local/deploy_demo_environment.sh
```

If `DBAI_AUTH_MODE` is unset, the script uses the existing Databricks CLI
profile and opens browser OAuth only when that profile is not authenticated.

### Initial VS Code OAuth setup

Complete this once on the machine where VS Code and the Databricks extension
run:

1. Install or enable the **Databricks** extension for Visual Studio Code.
2. Open this repository folder in VS Code.
3. Open the Databricks activity bar and locate the **Configuration** view.
4. Select **Auth Type**, then select the gear icon for **Sign in to Databricks workspace**.
5. In the Command Palette, choose **OAuth (user to machine)**.
6. Enter `aiarchitect` as the authentication profile name.
7. In the Configuration view, select **Login to Databricks**.
8. In the Command Palette, select the `aiarchitect` profile.
9. Complete the browser sign-in and approve `all-apis` access if prompted.
10. Return to VS Code and confirm that the Configuration view shows the target
        workspace and OAuth authentication.

The extension stores the OAuth profile for the Databricks CLI and SDKs. The
repository also ignores the extension's local `.databricks/` state; do not
check credentials or generated workspace state into source control.

If the `aiarchitect` profile already exists, select it in step 8 and complete
the browser sign-in instead of creating another profile.

### Verify terminal authentication

Open a new terminal, or export the profile in the current terminal. The
environment variable must be set in every shell that runs a Databricks command:

```bash
export DATABRICKS_CONFIG_PROFILE=aiarchitect
export DATABRICKS_SQL_WAREHOUSE_ID=<serverless-sql-warehouse-id>
databricks auth describe -p aiarchitect
databricks current-user me -p aiarchitect
```

The first command should report the configured workspace and OAuth profile. The
second should return your Databricks user details. If either command fails,
repeat the VS Code login steps or run this terminal fallback:

```bash
databricks auth login aiarchitect \
    --host https://adb-7405616725207770.10.azuredatabricks.net
export DATABRICKS_CONFIG_PROFILE=aiarchitect
```

The SQL warehouse variable is needed only for catalog DDL and Genie setup. Do
not set it for teardown when the SQL warehouse has already been deleted.

For local App development, create the ignored root dotenv file:

```bash
cp .env.example .env
# Edit .env and replace <serverless-sql-warehouse-id> with a real warehouse ID.
```

The App loads this file automatically when started from the repository. The
`DATABRICKS_BUNDLE_VAR_*` entries are optional; they let the bundle CLI reuse
the same values:

```bash
set -a
source .env
set +a
databricks bundle deploy -t dev
databricks bundle run supply_chain_agent -t dev
```

Do not commit `.env`. Authentication remains managed by the Databricks OAuth
profile; no token belongs in this file.

### Create a disposable Azure environment

For a clean demo workspace, authenticate to Azure and run the repository
orchestrator from the repository root:

```bash
az login
export AZURE_SUBSCRIPTION_ID=<subscription-id>
export AZURE_LOCATION=eastus2
scripts/local/deploy_demo_environment.sh
```

The script creates the dedicated `rg-dbai-demo` resource group, an Azure
Databricks Premium workspace, and a single-node serverless SQL warehouse when
one is not supplied. It then passes the new workspace URL and warehouse ID to
the Bundle and bootstrap scripts. It uses the workspace's isolated catalog
(`dbai_<environment>_<workspace-id>`) by default. Set `DBAI_CATALOG` to override
it, or set `DATABRICKS_SQL_WAREHOUSE_ID` to reuse a warehouse instead; a reused
or user-supplied warehouse is not deleted by the teardown script.

Preview the complete Azure teardown without contacting the platform:

```bash
scripts/local/destroy_demo_environment.sh --yes --dry-run
```

After reviewing the plan, destroy the Databricks data-plane objects first and
then the dedicated Azure resource groups:

```bash
scripts/local/destroy_demo_environment.sh --yes
```

The wrapper refuses to delete Azure resources if Databricks authentication or
data-plane cleanup fails. It deletes the SQL warehouse only when this
deployment created and recorded it. The existing `rgdata` resource group is
never targeted.

Run the complete bootstrap from the repository root:

```bash
/opt/az/bin/python3 scripts/local/bootstrap_demo_environment.py
```

Bootstrap is idempotent. It deploys the bundle, recreates the structured demo
tables, uploads the three baseline contracts, runs a full contract rebuild,
provisions the Search endpoint and index when missing, and creates the Genie
function and table metadata. It retains existing Search objects rather than
recreating them.

Bootstrap runs a workspace compatibility gate after the refresh job and before
AI Search provisioning. To run the gate independently:

```bash
/opt/az/bin/python3 scripts/local/validate_demo_workspace.py
```

It verifies that the AI Search source is a regular Delta table with Change Data
Feed and the expected schema. It rejects Streaming Tables and Materialized
Views, and checks SQL access plus the embedding and chat model endpoints. See
[Deployment Compatibility and Agent Review](06-deployment-compatibility-and-agent-review.md)
for the full checklist and AI-assisted review guidance.

After bootstrap, manually trigger one AI Search sync from the index page and
wait for **Online** and **Completed** status before testing retrieval.

### Custom Databricks App Agent

The bundle also deploys `supply_chain_agent`, a custom Databricks App that
hosts the MLflow AgentServer, OpenAI Agents SDK orchestration, and a dedicated
chat interface. The agent uses the existing Gold tables and
`search_vendor_contracts` function; it does not create a second data store.

Deploy it with the serverless SQL warehouse used by the demo:

```bash
databricks bundle deploy -t dev \
    --var="sql_warehouse_id=<serverless-sql-warehouse-id>"
databricks bundle run supply_chain_agent -t dev
```

The model defaults to `databricks-llama-4-maverick`. Override it when another
foundation model is enabled in the workspace:

```bash
databricks bundle deploy -t dev \
    --var="sql_warehouse_id=<serverless-sql-warehouse-id>" \
    --var="model_endpoint=<available-model-endpoint>"
```

Open the app URL shown by the Databricks Apps page. Each request uses the
signed-in user's authorization for SQL. The app requests the `sql` and
`model-serving` user scopes, so users must consent when prompted. Grant users
`CAN USE` on the SQL warehouse and the required Unity Catalog privileges on
the three Gold tables and `search_vendor_contracts`.

This makes the app suitable for the RBAC demonstration: Unity Catalog table
permissions, row filters, and column masks are evaluated for the current user.
The SQL tool also enforces a read-only allow-list and a 100-row maximum.

To remove the disposable environment, preview the plan first:

```bash
/opt/az/bin/python3 scripts/local/destroy_demo_environment.py --dry-run
```

Then run the destructive teardown:

```bash
/opt/az/bin/python3 scripts/local/destroy_demo_environment.py --yes
```

Teardown deletes the Search index and endpoint, Volume files and object, Genie
function, all seven demo tables, the custom App, and bundle-managed jobs without
requiring a SQL warehouse. It uses Databricks workspace APIs for Unity Catalog
deletion. The selected isolated catalog is retained. Use
`--keep-bundle-resources` to retain the App and jobs or `--drop-schema` when the
schema is dedicated exclusively to this demo.

### 1. Create the Unity Catalog Volume

The bootstrap script creates the required volume automatically. Create it
manually only when running the individual upload and refresh steps:

```text
globalmart.supply_chain.vendor_contracts
```

In Catalog Explorer:

1. Open **Catalog**
2. Select catalog `globalmart`
3. Select schema `supply_chain`
4. Select **Create > Volume**
5. Name it `vendor_contracts`
6. Choose a managed volume

The resulting path is:

```text
/Volumes/globalmart/supply_chain/vendor_contracts
```

You need `USE CATALOG`, `USE SCHEMA`, and `WRITE VOLUME` permissions.

### 2. Upload contract files

Supported extensions in the refresh job are:

```text
.txt
.md
.csv
.json
.html
.pdf
```

#### Option A: Catalog Explorer

1. Open the `vendor_contracts` volume.
2. Select **Upload files**.
3. Choose one or more local files.
4. Upload them.

You can organize files in subdirectories, for example:

```text
/Volumes/globalmart/supply_chain/vendor_contracts/vendor_a/contract.pdf
/Volumes/globalmart/supply_chain/vendor_contracts/vendor_b/terms.md
```

#### Option B: Databricks Python SDK

Install the SDK locally:

```bash
pip install databricks-sdk
```

Authenticate using your existing Azure Databricks profile:

```bash
databricks auth profiles
databricks auth login aiarchitect --host https://adb-7405616725207770.10.azuredatabricks.net
export DATABRICKS_CONFIG_PROFILE=aiarchitect
databricks auth describe -p aiarchitect
databricks current-user me -p aiarchitect
```

The login command opens a browser and stores OAuth credentials in the local
Databricks CLI profile. `WorkspaceClient` uses that profile directly. The AI
Search SDK is bridged by the repository scripts through `databricks auth token`,
so do not manually set `DATABRICKS_HOST` or copy a token for the normal flow.

Upload a file:

```python
from databricks.sdk import WorkspaceClient

client = WorkspaceClient()

with open("./contract.pdf", "rb") as contents:
    client.files.upload(
    "/Volumes/globalmart/supply_chain/vendor_contracts/contract.pdf",
    contents,
    overwrite=True,
    )
```

Upload multiple files:

```python
from pathlib import Path
from databricks.sdk import WorkspaceClient

client = WorkspaceClient()
volume_path = "/Volumes/globalmart/supply_chain/vendor_contracts"

for local_file in Path("./contracts").iterdir():
    if local_file.suffix.lower() in {
        ".txt", ".md", ".csv", ".json", ".html", ".pdf"
    }:
        destination = f"{volume_path}/{local_file.name}"
        with local_file.open("rb") as contents:
            client.files.upload(destination, contents, overwrite=True)
        print(f"Uploaded {local_file} to {destination}")
```

### 3. Deploy the bundle

From the project root:

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev
```

The bundle deploys three serverless jobs:

- `generate_mock_data` creates the structured demo tables.
- `refresh_vendor_contract_chunks` creates the regular Delta source for AI Search.
- `upsert_demo_vendor` adds the new `VEND-321` row used by the lifecycle demo.

### 4. Generate structured demo data

Run:

```bash
databricks bundle run generate_mock_data -t dev
```

### 5. Refresh contract chunks

Run:

```bash
databricks bundle run refresh_vendor_contract_chunks -t dev
```

The refresh job reads from:

```text
/Volumes/globalmart/supply_chain/vendor_contracts
```

It classifies file changes, extracts only new or updated documents, removes deleted-document chunks, and writes 500-token windows with a 50-token overlap to:

```sql
SELECT
  source_file,
  chunk_index,
  token_count,
  LEFT(chunk_text, 200) AS preview
FROM globalmart.supply_chain.vendor_contract_chunks_index_source
ORDER BY source_file, chunk_index;
```

The default mode is incremental. For a complete recovery rebuild, set `INGESTION_MODE=full_rebuild` for the job run.

The former Lakeflow pipeline and its duplicate tables have been removed.

For a repeatable add, update, and delete demonstration, including the new `VEND-321` vendor association and verification queries for Bronze, Silver, Gold, and AI Search, see [Contract Change Demo Runbook](05-contract-change-demo.md).

### 5. Create the AI Search endpoint

The bootstrap script creates the endpoint when it is missing. Create it
manually only when using the individual index setup flow.

In Azure Databricks:

1. Open **Compute**
2. Open the **AI Search** tab
3. Create an endpoint
4. Use the endpoint name configured by the script:

```text
globalmart-supply-chain-search
```

Or set a different endpoint name:

```bash
export AI_SEARCH_ENDPOINT=<your-endpoint-name>
```

### 6. Execute `create_vendor_contract_index.py`

From the project root:

```bash
export AI_SEARCH_ENDPOINT=globalmart-supply-chain-search

/opt/az/bin/python3 scripts/local/create_vendor_contract_index.py
```

The script creates:

```text
globalmart.supply_chain.vendor_contract_chunks_index_rebuilt
```

It configures:

- Source table: `globalmart.supply_chain.vendor_contract_chunks_index_source`
- Primary key: `chunk_id`
- Text column: `chunk_text`
- Embedding model: `databricks-qwen3-embedding-0-6b`
- Sync mode: `TRIGGERED`

The script is idempotent. If the index already exists, it prints:

```text
Index already exists: globalmart.supply_chain.vendor_contract_chunks_index_rebuilt
```

### 7. Configure Genie

Run `sql/01_genie_search.sql` in a serverless SQL warehouse if the bootstrap did
not create the function. Add these objects to the Genie space:

- `globalmart.supply_chain.dim_products`
- `globalmart.supply_chain.dim_vendors`
- `globalmart.supply_chain.fact_inventory_status`
- `globalmart.supply_chain.search_vendor_contracts`

Paste these instructions into the Genie space instructions:

```text
Use SQL against dim_products, dim_vendors, and fact_inventory_status for exact
numeric answers, joins, totals, dates, inventory values, and account-manager
questions. Use search_vendor_contracts for vendor-contract language, penalties,
weather exceptions, service levels, liability, and delivery obligations.

For a hybrid question, use SQL for the structured fact and
search_vendor_contracts for contract evidence, then clearly separate the two.
When calling search_vendor_contracts, pass vendor_id, support_tier, and region
when the question provides them. Cite source_file and summarize the returned
chunk evidence. Do not invent contract terms or use structured table values as
contract evidence.

The Search index contains current active Gold contract chunks only. Bronze event
history and Silver lifecycle records are audit data and are not searchable
contract content. If the function returns no rows for a deleted vendor or file,
say that no active searchable contract is available; do not answer from deleted
contract history.
```

Use the read-only smoke tests in `sql/02_genie_smoke_tests.sql` after manually
synchronizing the triggered index.

After adding or replacing files, rerun the refresh job and trigger the
`TRIGGERED` index update before using Genie.

## Important prerequisite

The AI Search Delta Sync source must support Change Data Feed. If index creation reports a Change Data Feed requirement, enable it on the active source table before creating the index:

```sql
ALTER TABLE globalmart.supply_chain.vendor_contract_chunks_index_source
SET TBLPROPERTIES (delta.enableChangeDataFeed = true);
```

The Azure-specific references are:

- [Azure Databricks Volumes](https://learn.microsoft.com/en-us/azure/databricks/volumes/)
- [Azure Databricks AI Search](https://learn.microsoft.com/en-us/azure/databricks/generative-ai/create-query-vector-search)
