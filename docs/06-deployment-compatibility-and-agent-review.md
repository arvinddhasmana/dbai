# Deployment Compatibility and Agent Review

## Purpose

This demo uses an AI-assisted development workflow, but live Databricks
capabilities remain the source of truth. Generated code must be reviewed against
the target workspace before it creates or changes platform objects.

The most important compatibility rule in this repository is:

> The managed AI Search Delta Sync index must read from a regular Delta table.
> Do not use a Streaming Table or Materialized View as its source.

The source table is:

```text
globalmart.supply_chain.vendor_contract_chunks_index_source
```

The managed index is:

```text
globalmart.supply_chain.vendor_contract_chunks_index_rebuilt
```

## Preflight Gate

Run the preflight after the contract refresh job has created the source table
and before creating or updating the AI Search index:

```bash
export DATABRICKS_CONFIG_PROFILE=aiarchitect
export DATABRICKS_SQL_WAREHOUSE_ID=<serverless-sql-warehouse-id>

/opt/az/bin/python3 scripts/local/validate_demo_workspace.py
```

The check validates:

- The SQL warehouse exists and can be used for a test query.
- The source is a regular managed or external Delta table.
- The source is not a View, Streaming Table, or Materialized View.
- Change Data Feed is enabled.
- The source contains every column configured for AI Search synchronization.
- The current identity can query the source table.
- The embedding endpoint exists and is `READY`.
- The configured chat model endpoint exists and is `READY`.

After the index exists, verify the index as well:

```bash
/opt/az/bin/python3 scripts/local/validate_demo_workspace.py --require-index
```

The bootstrap script runs the first form automatically immediately before
index provisioning. A failed check stops bootstrap before the index creation
call. This is deliberately a small gate for the demo, not a replacement for a
full production deployment pipeline.

## What Is Enforced Where?

### Application checks

The app validates model-generated SQL in `app/agent_server/sql_guard.py`:

- Only one `SELECT` or `WITH` statement is accepted.
- Only the three approved structured Gold tables are allowed.
- Write and administrative operations are rejected.
- Results are limited to 100 rows.

The app also passes the signed-in Databricks user token to SQL execution when
the App supplies `x-forwarded-access-token`.

### Unity Catalog checks

Unity Catalog is responsible for data authorization:

- **Row filters** restrict which rows a user can see.
- **Column masks** hide or transform sensitive column values.
- **Privileges** decide whether a user can access the catalog, schema, table,
  function, or SQL warehouse.

These policies are not defined by the conversational prompt. Databricks applies
them while executing SQL for the current user. A successful query can return
fewer rows or masked values; a missing privilege can make the query fail.

### AI Search checks

The contract index is a separate serving copy. Do not assume a row filter or
column mask on the source table automatically protects content already copied
into an AI Search index. This demo uses non-sensitive contract data and
business filters for retrieval, but those filters are not authorization.

If contracts become user-sensitive, choose an explicit design before indexing:

- Separate indexes by security domain.
- Index only content that every index consumer may read.
- Use an identity-aware retrieval layer with enforced authorization.
- Keep sensitive retrieval in governed SQL or another policy-enforcing service.

## Agent-Assisted Review

An AI coding agent is useful for reviewing diffs and finding likely platform
mistakes. Give it these repository rules:

```text
Treat the target Databricks workspace as authoritative. Before changing AI
Search, inspect the source table type, Delta format, Change Data Feed, primary
key, synced columns, embedding endpoint, and existing index configuration.
Never replace the regular Delta source with a Streaming Table or Materialized
View. Do not treat business filters as authorization. Do not claim row-filter
or column-mask protection for AI Search without an explicit design and test.
```

The agent should produce a review containing:

1. The affected Databricks resources.
2. The workspace facts it verified.
3. Compatibility risks and missing permissions.
4. The exact preflight or smoke test that will disprove each assumption.
5. A minimal proposed change.

The agent is advisory. It must not substitute for the executable preflight,
bundle validation, deployment, index synchronization, and end-to-end smoke
tests.

## Demo Smoke Tests

After deployment and a completed AI Search sync, test these paths:

| Question type | Expected path | Expected evidence |
|---|---|---|
| Inventory facts | `query_inventory` | Current SQL-visible rows |
| Contract language | `search_vendor_contracts` | Source file and chunk citation |
| Mixed question | Both tools | Facts and contract evidence separated |
| Insufficient access | SQL or retrieval failure | Transparent limitation, no invented data |

For an RBAC demonstration, run the same question as two users with different
Unity Catalog permissions. Confirm that row-filtered or masked SQL results are
different, and separately verify the behavior of contract retrieval. Do not
infer AI Search authorization from the SQL result alone.

## Conversation State

The demo UI keeps the current conversation in browser memory and sends the
conversation input with each `/invocations` request. This keeps the App
stateless and avoids adding a database to the demo.

Consequences:

- A browser refresh loses the conversation.
- Another device does not see the conversation.
- Long conversations increase request size and model token usage.

For production, store a user-owned `conversation_id`, messages, tool calls, and
answers on the server. Load that history on each request and enforce ownership
with the signed-in Databricks identity. Do not treat assistant history supplied
by the browser as an audit record.