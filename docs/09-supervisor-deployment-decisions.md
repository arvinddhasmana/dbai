# Supervisor Deployment Decisions

## Fixed Decisions

| Area | Decision |
| --- | --- |
| User-facing runtime | New Databricks App and MLflow Responses Agent Server |
| Orchestration | OpenAI Agents SDK `Agent` and `Runner` |
| Contract data | Native managed Vector Search MCP |
| Inventory data | Genie MCP over `globalmart.supply_chain` inventory tables |
| Authentication | OBO forwarded access token |
| Conversation state | Lakebase `AsyncDatabricksSession` |
| v1 capabilities | Read-only retrieval and analysis |
| Existing contract App | Remains standalone and unchanged |
| Future actions | Separate tools with explicit human approval |

## Reused Resources

The supervisor binds the existing contract data foundation instead of creating duplicate resources:

- Catalog/schema: `globalmart.supply_chain`.
- Managed Vector Search index: `globalmart.supply_chain.vendor_contract_chunks_index_rebuilt`.
- AI Search endpoint: `globalmart-supply-chain-search`.
- Inventory tables: `fact_inventory_status`, `dim_products`, and `dim_vendors`.

## Required Workspace Inputs

These values are intentionally deployment inputs, not hard-coded secrets or guessed IDs:

- `GENIE_SPACE_ID`: `01f1ab3249ea18269d5edc4f599b895c` (Supply Chain Inventory Management).
- `MLFLOW_EXPERIMENT_ID`: `4341372968956549` (`/Shared/globalmart-supply-chain-agent-uc-v2-dev`).
- `LAKEBASE_BRANCH` and `LAKEBASE_DATABASE`: the dedicated `globalmart-supervisor-memory` autoscaling resources.
- `DATABRICKS_CONFIG_PROFILE`: the CLI profile used for validation and deployment.
- `MODEL_ENDPOINT`: a Responses-compatible Databricks model endpoint available to the App.
- User API scopes include `sql`, `genie`, `model-serving`, and `vector-search` so the supervisor can call the inventory and managed contract MCP servers on behalf of the signed-in user.

The bundle variables are in `agents/supply_chain_supervisor/resources/supervisor.resources.yml`. Replace the placeholder values before deployment. Do not commit access tokens, client secrets, or `.env` files.

## Permission Model

The App resource grants:

- `CAN_RUN` on the configured Genie space.
- `SELECT` on the existing Vector Search index.
- `CAN_CONNECT_AND_CREATE` on the configured Lakebase database.
- MLflow experiment management for trace publication.
- App user API scopes for SQL, Genie, model serving, and Vector Search.

End users still need the underlying Genie and Unity Catalog permissions required by the OBO request. App binding alone should not be treated as a bypass of data governance.

MLflow trace publication also requires explicit Unity Catalog grants for the App service principal. The MLflow experiment resource binding does not grant access to the trace schema or existing trace tables. After deployment, run the existing helper with the deployed App name and SQL warehouse:

```bash
DATABRICKS_CONFIG_PROFILE=<profile> \
DATABRICKS_SQL_WAREHOUSE_ID=<warehouse-id> \
DBAI_CATALOG=globalmart \
uv run python scripts/local/grant_data_access.py \
	--app-name agent-supply-chain-sup-dev \
	--user-principal ""
```

This grants the App identity `USE CATALOG`, `USE SCHEMA`, and `SELECT`/`MODIFY` on the existing MLflow trace tables. Verify the effective grants with `SHOW GRANTS`, then restart or run the bundle App and make a fresh request before treating tracing as healthy.

## Deliberate Non-Decisions

- Do not wrap the existing contract App as a supervisor tool; this adds latency and loses the direct managed MCP contract.
- Do not expose arbitrary SQL; Genie remains the governed Text-to-SQL boundary.
- Do not add write tools in v1.
- Do not use the `rag-chat` pgvector implementation; the requirement is the existing managed AI Search index.