# GlobalMart Supply-Chain Supervisor

This App is the user-facing supervisor for GlobalMart supply-chain questions. It uses the OpenAI Agents SDK with two managed Databricks MCP servers:

- Managed Vector Search MCP for vendor contract evidence.
- Genie MCP for inventory Text-to-SQL and metrics.

The App uses OBO for user authorization and Lakebase `AsyncDatabricksSession` for durable conversation history. Without Lakebase configuration, it accepts request-carried history for local development. V1 is read-only.

## Configuration

Required deployment values:

```text
GENIE_SPACE_ID
MLFLOW_EXPERIMENT_ID
LAKEBASE_BRANCH
LAKEBASE_DATABASE
MODEL_ENDPOINT
```

The existing managed Vector Search index defaults to `globalmart.supply_chain.vendor_contract_chunks_index_rebuilt`. Override `AI_SEARCH_INDEX` only when intentionally binding a different three-part index name.

## Local Development

From this directory:

```bash
uv sync
uv run start-server
```

The server exposes the Responses API at `http://localhost:8000/invocations` for local development and `/api/invocations` through the deployed App, plus a simple chat page at `/` and `/health`. Local requests need a Databricks environment configured by the SDK. App OBO headers are supplied automatically only when the request enters through a Databricks App.

Run the focused checks with:

```bash
uv run --with pytest pytest -q tests/test_history.py tests/test_configuration.py
uv run python -m compileall -q agent_server
```

## Deployment

Replace the placeholders in `resources/supervisor.resources.yml`, then validate and deploy with the profile from your Databricks configuration:

```bash
databricks bundle validate -t dev --profile <profile>
databricks bundle deploy -t dev --profile <profile>
```

The App must be granted `CAN_RUN` on the Genie space, `SELECT` on the Vector Search index, and Lakebase connect/create access. End users also need the underlying governed data permissions for OBO calls.

See [the architecture decision](../../docs/08-supervisor-agent-architecture.md), [deployment decisions](../../docs/09-supervisor-deployment-decisions.md), and [validation runbook](../../docs/10-supervisor-validation-runbook.md) for the full design and smoke-test matrix.