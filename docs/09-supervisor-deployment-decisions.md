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
| Evaluation boundary | Live evaluation calls the deployed Supervisor App over authenticated REST at `/api/invocations`; it does not import or call App internals |
| Trace correlation | Supervisor traces and evaluation runs share experiment `4341372968956549`; W3C `traceparent` propagation links `predict_fn` to the App hierarchy |
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
- `MLFLOW_TRACING_SQL_WAREHOUSE_ID`: the SQL warehouse used by MLflow to read and assess UC-backed traces; development value `a749a7ee30b8f4f4`.
- `MLFLOW_TRACE_CATALOG`, `MLFLOW_TRACE_SCHEMA`, and `MLFLOW_TRACE_TABLE_PREFIX`: the shared trace location, currently `globalmart.agent_observability.contract_agent_traces`.
- `LAKEBASE_BRANCH` and `LAKEBASE_DATABASE`: the dedicated `globalmart-supervisor-memory` autoscaling resources.
- `DATABRICKS_CONFIG_PROFILE`: the CLI profile used for validation and deployment.
- `MODEL_ENDPOINT`: a Responses-compatible Databricks model endpoint available to the App.
- User API scopes include `sql`, `genie`, `model-serving`, and `vector-search` so the supervisor can call the inventory and managed contract MCP servers on behalf of the signed-in user.

The bundle variables are in `agents/supply_chain_supervisor/resources/supervisor.resources.yml`. Replace the placeholder values before deployment. Do not commit access tokens, client secrets, or `.env` files.

## Permission Model

The App resource grants:

- `CAN_RUN` on the configured Genie space.
- Vector Search MCP remains optional at runtime; if its index or endpoint is unavailable, the Supervisor records the failure and continues with Genie.
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

## App Destroy and Redeploy Lifecycle

Destroying a Contract or Supervisor App bundle and deploying it again recreates
the Databricks App source, configuration, API scopes, and manifest-declared
bindings. It does not recreate the shared resources referenced by those
bindings, including the SQL warehouse, MLflow experiment and trace data,
Unity Catalog objects, Genie space, Vector Search endpoint/index, model
endpoint, or Lakebase project. Existing Lakebase data is external to the App;
verify that the project, branch, and database still exist instead of assuming
that an App destroy preserved them.

Manual grants are identity-bound and must be reapplied. The recreated App may
have a new service principal/client ID and OAuth identity, and its URL may
change. Grants for the old App identity do not automatically transfer to the
new one. The evaluation dataset and evaluation Job are also separate from the
two agent bundles and are not recreated by destroying or deploying those
bundles.

For a disposable App-only rebuild, record the current App URLs, App service
principal IDs, bundle target, catalog, experiment, Lakebase paths, and SQL
warehouse first. Destroy and deploy each agent bundle with the explicit
warehouse variable, then run `scripts/local/grant_data_access.py` for each new
App identity. Reconcile Supervisor evaluation permissions with
`scripts/deployable/grant_supervisor_evaluation_access.py`, verify the
effective grants, and update the evaluation Job's `SUPERVISOR_APP_URL` if the
Supervisor URL changed. Run a health/direct smoke test before starting live
evaluation. Prefer an ordinary bundle deploy and App restart for code or
configuration changes when preserving the current App identity and URL matters.

## Evaluation and Trace Boundary

Live Supervisor evaluation is intentionally a black-box App evaluation. The Job loads the governed MLflow `EvaluationDataset`, creates an MLflow `predict_fn` span for each case, and sends the case question as an authenticated HTTP `POST` to the deployed App's `/api/invocations` route. The App route forwards to MLflow AgentServer's `/invocations` handler, which calls `invoke_handler` and runs the supervisor, model, Genie, and Vector Search MCP spans.

The evaluation adapter adds the W3C `traceparent` header to the App request. App middleware activates that incoming context before AgentServer handles the request. `MLFLOW_TRACE_PROPAGATE_TO_OTEL_CONTEXT=true` is configured in both the evaluator and App so MLflow and OpenTelemetry instrumentation see the same active context. The resulting hierarchy is:

```text
predict_fn
└── invoke_handler
	├── supervisor.mcp
	├── AgentRunner.run
	├── call_tool
	└── model and MCP spans
```

A direct curl or UI request still exercises the same App REST route and produces the App-side hierarchy, but it has no evaluation `predict_fn` parent. The shared URL and experiment alone do not join traces; the distributed `traceparent` header is the correlation mechanism.

## Deliberate Non-Decisions

- Do not wrap the existing contract App as a supervisor tool; this adds latency and loses the direct managed MCP contract.
- Do not expose arbitrary SQL; Genie remains the governed Text-to-SQL boundary.
- Do not add write tools in v1.
- Do not use the `rag-chat` pgvector implementation; the requirement is the existing managed AI Search index.