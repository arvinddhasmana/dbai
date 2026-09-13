# Supervisor Validation Runbook

## Local Checks

Run from `agents/supply_chain_supervisor`:

```bash
uv run --with pytest pytest -q tests/test_history.py tests/test_configuration.py
uv run python -m compileall -q agent_server
```

The local package tests do not call Databricks. Runtime smoke tests require App OBO headers and configured MCP resources.

## Configure Deployment Inputs

Set or export the following values in the deployment environment:

```bash
export DATABRICKS_CONFIG_PROFILE=<profile>
export DATABRICKS_SQL_WAREHOUSE_ID=<sql-warehouse-id>
export DATABRICKS_BUNDLE_VAR_sql_warehouse_id="$DATABRICKS_SQL_WAREHOUSE_ID"
export GENIE_SPACE_ID=01f1ab3249ea18269d5edc4f599b895c
export MLFLOW_EXPERIMENT_ID=4341372968956549
export MLFLOW_TRACE_PROPAGATE_TO_OTEL_CONTEXT=true
export LAKEBASE_BRANCH=projects/globalmart-supervisor-memory/branches/production
export LAKEBASE_DATABASE=projects/globalmart-supervisor-memory/branches/production/databases/databricks-postgres
```

The Supervisor shares the contract App's MLflow experiment and UC trace tables:
`globalmart.agent_observability.contract_agent_traces_otel_*`. The App service
principal needs `USE CATALOG`, `USE SCHEMA`, `SELECT`, and `MODIFY` on those
existing tables. Run `scripts/local/grant_data_access.py` after deployment to
repair these grants. The same values can be supplied as bundle variable
overrides. Confirm the Genie space is configured for
`globalmart.supply_chain.fact_inventory_status`, `dim_products`, and
`dim_vendors`, with useful table comments and joins.

## Bundle Validation and Deployment

All CLI commands must use the configured profile:

```bash
cd agents/supply_chain_supervisor
databricks bundle validate -t dev --profile "$DATABRICKS_CONFIG_PROFILE"
databricks bundle deploy -t dev --profile "$DATABRICKS_CONFIG_PROFILE"
```

If validation reports an unsupported resource or placeholder, correct the bundle variable rather than deploying a guessed resource. Keep the existing contract App deployment independent.

### Rebuild After App Bundle Destruction

Use this procedure only when the App itself must be recreated. A normal
`bundle deploy` and App restart is sufficient for code or configuration
changes. Before a destructive rebuild, record the current App URL and service
principal ID, the target, catalog, shared experiment ID, Lakebase paths, and
SQL warehouse ID.

The bundle recreates the App and its declared experiment, warehouse, Genie,
and Lakebase bindings. It does not recreate the referenced SQL warehouse,
MLflow experiment or trace data, Unity Catalog objects, Genie space, Vector
Search endpoint/index, model endpoint, Lakebase project, evaluation dataset,
or evaluation Job. Existing Lakebase data must be verified separately. A new
App identity can also invalidate manual UC, trace-table, and Vector Search
endpoint grants.

Destroy and redeploy the two agent bundles separately, using the current
resource values and an explicit warehouse ID:

```bash
cd agents/supply_chain_agent
databricks bundle destroy -t dev --profile "$DATABRICKS_CONFIG_PROFILE" --auto-approve
databricks bundle deploy -t dev \
  --profile "$DATABRICKS_CONFIG_PROFILE" \
  --var=sql_warehouse_id="$DATABRICKS_SQL_WAREHOUSE_ID"

cd ../supply_chain_supervisor
databricks bundle destroy -t dev --profile "$DATABRICKS_CONFIG_PROFILE" --auto-approve
databricks bundle deploy -t dev \
  --profile "$DATABRICKS_CONFIG_PROFILE" \
  --var=sql_warehouse_id="$DATABRICKS_SQL_WAREHOUSE_ID"
```

After each deployment, run or restart the App, query its current URL and
service principal, and reapply access:

```bash
cd ../..
uv run python scripts/local/grant_data_access.py \
  --app-name agent-supply-chain-ctr-ka-dev \
  --catalog "$DBAI_CATALOG" \
  --warehouse-id "$DATABRICKS_SQL_WAREHOUSE_ID" \
  --user-principal ""

uv run python scripts/local/grant_data_access.py \
  --app-name agent-supply-chain-sup-dev \
  --catalog "$DBAI_CATALOG" \
  --warehouse-id "$DATABRICKS_SQL_WAREHOUSE_ID" \
  --user-principal ""
```

Rerun `scripts/deployable/grant_supervisor_evaluation_access.py` for the
recreated Supervisor identity, verify `SHOW GRANTS`, and rerun
`provision_supervisor_evaluation.py` only when the evaluation experiment or
dataset needs reconciliation. The separate evaluation Job survives the two
agent bundle operations, but its `SUPERVISOR_APP_URL` must be updated if the
new Supervisor URL differs. Finish with `/health`, a direct invocation, and a
live evaluation trace check.

The Vector Search MCP server remains configured so its unavailable state is
observable. If its endpoint has been removed, the Supervisor records an
`unavailable` `supervisor.mcp` span with the error type, marks that source
unavailable in the response metadata, and continues with Genie for inventory
questions. It must not invent contract answers.

## Supervisor Evaluation

The reusable supervisor evaluator is in [supervisor_evaluation](../agents/supply_chain_supervisor_evaluation/). It reads the governed MLflow EvaluationDataset `globalmart.agent_evaluation.supervisor_cases` and writes evaluation runs to the shared Supervisor trace experiment `/Shared/globalmart-supply-chain-agent-uc-v2-dev` (experiment ID `4341372968956549`). The dataset currently contains six cases.

Validate and deploy only the evaluation bundle:

```bash
cd agents/supply_chain_supervisor_evaluation
databricks bundle validate -t dev --profile "$DATABRICKS_CONFIG_PROFILE"
databricks bundle deploy -t dev --profile "$DATABRICKS_CONFIG_PROFILE"
```

Run the managed live evaluation by overriding the deployed supervisor App URL. The Job exchanges the notebook's internal token at `/oidc/v1/token` for an audience-scoped App token using the App OAuth client ID, then calls `/api/invocations`. The exchange must include `requested_token_type=urn:ietf:params:oauth:token-type:access_token` and `scope=all-apis`.

```bash
databricks jobs run-now --job-id 561535088082008 \
  --profile "$DATABRICKS_CONFIG_PROFILE" \
  --notebook-params '{"MODE":"live","SUPERVISOR_APP_URL":"<supervisor-app-url>"}'
```

The evaluation is a REST call to the deployed App, not an in-process call to the Supervisor Python handler and not a Model Serving invocation. The adapter sends `POST <SUPERVISOR_APP_URL>/api/invocations` with the case question. While MLflow's `predict_fn` span is active, it adds a W3C `traceparent` header; the App middleware restores that context before AgentServer invokes `invoke_handler`. Both runtimes must set `MLFLOW_TRACE_PROPAGATE_TO_OTEL_CONTEXT=true`.

The evaluation publishes exactly four aggregate metrics: `supervisor_correctness`, `supervisor_relevance_to_query`, `required_fact_coverage`, and `tool_routing_accuracy`. Confirm six `OK` traces, one linked evaluation trace per case, and for each trace a `predict_fn` span containing `invoke_handler` plus nested supervisor, model, Genie, or Vector Search spans. Also confirm no response errors and sanitized `evaluation.tool_families`/`evaluation.tool_calls` metadata. Never treat a run containing authentication or trace-linkage errors as a functional evaluation.

The `jobs run-now` command can time out after submitting the run. Do not submit a duplicate immediately; use `databricks jobs list-runs --job-id 561535088082008` and `databricks jobs get-run --run-id <run-id>` to find and monitor the submitted run.

## Runtime Smoke Tests

After the App is running, exercise `/health` and `/api/invocations` through the authenticated App URL. Test these cases:

1. Contract-only: vendor obligations, service levels, penalties, or weather exceptions.
2. Inventory-only: quantity, value, vendor, warehouse, or transit metrics.
3. Hybrid: delayed inventory plus the applicable contract remedy.
4. Follow-up: reuse the same `session_id` and confirm context is retained.
5. Empty result: verify the response says no evidence was found.
6. Tool failure: verify the response identifies the unavailable source and does not guess.
7. Isolation: verify a second user cannot read the first user's Lakebase session.
8. Streaming: verify tool events and final text arrive without an MCP connection-closed error.
9. Degraded Vector Search: with the endpoint unavailable, verify Genie inventory questions still answer and the shared trace contains `supervisor.mcp` with `status=unavailable`.

Example requests:

```bash
curl -X POST "$APP_URL/api/invocations" \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":[{"role":"user","content":"Which vendors have delayed inventory and what contract remedies apply?"}],"custom_inputs":{"session_id":"smoke-001"}}'

curl -N -X POST "$APP_URL/api/invocations" \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":[{"role":"user","content":"What are the weather-delay rules for VEND-789?"}],"stream":true,"custom_inputs":{"session_id":"smoke-002"}}'
```

Use a user token for OBO smoke tests. Do not paste tokens into source files or committed documentation.

These direct smoke requests validate the App independently of MLflow evaluation. They exercise the same `/api/invocations` REST route and should show the App-side `invoke_handler` hierarchy, but they do not have an evaluation `predict_fn` parent. Use the live evaluation trace check above to verify cross-process parent/child linkage.

## Observability and Exit Criteria

Inspect the MLflow trace for a hybrid request and confirm it contains supervisor, Genie, and managed AI Search spans without storing access tokens or unnecessary raw contract/SQL payloads. For a live evaluation, additionally confirm that each Evaluation Run trace contains `predict_fn` as the parent of the App's `invoke_handler` span. The implementation is ready for broader evaluation when the local checks pass, bundle validation succeeds, both MCP servers list tools under OBO, and the nine runtime cases above behave as expected.

After verification, stop only the temporary supervisor App and poll until its state is `STOPPED`. Leave the existing contract App in its current state.