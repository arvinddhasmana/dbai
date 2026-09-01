# GlobalMart Supply Chain RAG and Text-to-SQL Demo

## 1. Business Use Case

GlobalMart manages inventory across products, vendors, warehouses, and regional transportation networks. Operations teams need two kinds of answers:

1. **Structured answers:** Which inventory is delayed, what is its dollar value, and which account manager or vendor is responsible?
2. **Unstructured answers:** What do the vendor contracts say about delay penalties, weather exceptions, liability, and service levels?

This demo combines both patterns in Azure Databricks Premium:

- Delta tables answer inventory and vendor questions with SQL.
- Vendor contracts are loaded from a Unity Catalog Volume, parsed, chunked, and indexed for semantic retrieval.
- AI Search metadata enables filtering by vendor, support tier, and region.

### Business question demonstrated

> What is the value of delayed inventory associated with Sarah Jenkins?

The supplied data identifies Sarah Jenkins as the account manager for VEND-789. The delayed products are:

| Product | Stock on hand | Unit cost | Value |
|---|---:|---:|---:|
| P-101 EcoFlow Battery Pack | 50 | $250.00 | $12,500.00 |
| P-105 NeoGlow LED Strip | 0 | $12.00 | $0.00 |
| **Total** | | | **$12,500.00** |

### Contract question demonstrated

> What happens when a Gold Tier Midwest shipment is delayed by severe winter weather?

The VEND-789 contract states that severe winter weather, blizzards, and freezing conditions qualify as Force Majeure. Daily late penalties are waived, GlobalMart assumes temporary transit liability, and GlobalMart absorbs inventory holding costs during the freeze.

## 2. Demo Prerequisites

- Azure Databricks Premium workspace access.
- Unity Catalog access to catalog `globalmart` and schema `supply_chain`.
- Databricks CLI authenticated to the `aiarchitect` profile.
- Permission to create and delete the disposable AI Search endpoint and index.
- Access to the Databricks SQL Editor and AI Search index page.

Workspace used by this project:

`https://adb-7405616725207770.10.azuredatabricks.net`

## 3. Bootstrap The Demo

From the repository root:

```bash
export DATABRICKS_CONFIG_PROFILE=aiarchitect
export DATABRICKS_SQL_WAREHOUSE_ID=<serverless-sql-warehouse-id>
```

Run the complete idempotent bootstrap:

```bash
/opt/az/bin/python3 scripts/local/bootstrap_demo_environment.py
```

The bootstrap creates the catalog/schema/Volume, deploys the bundle, writes the
structured tables, uploads baseline contracts, rebuilds the contract layers,
creates the Search endpoint/index, and creates the Genie function. It does not
trigger the `TRIGGERED` index. Manually sync the index and wait for **Online**
and **Completed** before asking retrieval questions.

The bundle contains:

- `generate_mock_data`: writes the product, vendor, and inventory Delta tables.
- `refresh_vendor_contract_chunks`: reads contracts and writes the regular Delta index source.
- `upsert_demo_vendor`: adds the `VEND-321` vendor row for the add scenario.
- The obsolete Lakeflow pipeline and duplicate contract tables are no longer deployed.

The AI Search-compatible source is:

`globalmart.supply_chain.vendor_contract_chunks_index_source`

The managed index is:

`globalmart.supply_chain.vendor_contract_chunks_index_rebuilt`

## 4. Conversational Experiences

The demo exposes the same governed data through three complementary
experiences:

- **Genie Agent:** a managed SQL-first conversation over the structured tables
  and `search_vendor_contracts` function.
- **Custom Agent:** the Agent Framework orchestration in
  `app/agent_server/agent.py`, with read-only SQL, AI Search retrieval, and
  grounded citations.
- **Databricks App:** the dedicated UI and MLflow AgentServer host in `app/`.
  It hosts the Custom Agent and presents conversation state, evidence, and
  citations to the user.

The Custom Agent and App are one deployed product boundary, while Genie is the
parallel managed experience. Users can choose Genie for quick SQL-first
analysis or the App for explicit structured, contract, and hybrid orchestration.

## 5. Genie Agent Implementation

The first conversational implementation uses the Genie space with `sql/01_genie_search.sql`. Run that setup script in a serverless SQL warehouse when needed, then add `search_vendor_contracts` to the Genie space as a table-valued function. Read-only checks are in `sql/02_genie_smoke_tests.sql` and should run after index synchronization. The function queries the current hybrid AI Search index and exposes optional vendor, support-tier, and region filters.

Configure Genie with the following instructions:

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

## 6. Refresh the Demo Data

Refresh the structured business tables:

```bash
databricks bundle run generate_mock_data -t dev
```

Refresh the regular Delta contract source table:

```bash
databricks bundle run refresh_vendor_contract_chunks -t dev
```

The source files are under:

`/Volumes/globalmart/supply_chain/vendor_contracts`

The local sample contracts are under:

`sample_data/vendor_contracts/`

The refresh job reads the files, classifies new, updated, unchanged, and deleted files, extracts only affected documents, creates 500-token windows with a 50-token overlap, and incrementally updates the regular Delta source table. Use `INGESTION_MODE=full_rebuild` for recovery.

For the scripted contract lifecycle demonstration, including a new vendor contract, an amended existing contract, and a deletion with SQL inspection after each stage, follow [Contract Change Demo Runbook](05-contract-change-demo.md).

## 7. Refresh the Triggered AI Search Index

The index uses `TRIGGERED` mode. After the source refresh completes:

1. Open the Databricks AI Search page.
2. Open endpoint `globalmart-supply-chain-search`.
3. Open index `globalmart.supply_chain.vendor_contract_chunks_index_rebuilt`.
4. Select **Sync**, **Trigger update**, or the equivalent action shown by the workspace UI.
5. Wait until index status is **Online** and update status is **Completed**.

The bootstrap script is idempotent and only creates the index if it does not already exist:

```bash
export DATABRICKS_CONFIG_PROFILE=aiarchitect
export AI_SEARCH_ENDPOINT=globalmart-supply-chain-search
/opt/az/bin/python3 scripts/local/create_vendor_contract_index.py
```

The script uses the OAuth profile for the workspace SDK and obtains a short-lived
token internally for the AI Search SDK. No token copy/paste is required.

## 8. Present the Demo

### Part A: Explain the data model

Show the three tables:

- `globalmart.supply_chain.dim_products`
- `globalmart.supply_chain.dim_vendors`
- `globalmart.supply_chain.fact_inventory_status`

Explain that the fact table stores operational inventory status while dimension tables provide product and vendor context.

### Part B: Run the Text-to-SQL business query

In Databricks SQL Editor, run:

```sql
SELECT
  SUM(f.stock_on_hand * p.unit_cost_usd) AS delayed_inventory_value_usd
FROM globalmart.supply_chain.fact_inventory_status f
JOIN globalmart.supply_chain.dim_products p
  ON f.product_id = p.product_id
JOIN globalmart.supply_chain.dim_vendors v
  ON f.vendor_id = v.vendor_id
WHERE f.transit_status = 'Delayed in Transit'
  AND v.account_manager = 'Sarah Jenkins';
```

Expected result: `$12,500.00`.

### Part C: Show the contract source

Run:

```sql
SELECT
  source_file,
  vendor_id,
  support_tier,
  region_covered,
  chunk_index,
  token_count,
  LEFT(chunk_text, 180) AS chunk_preview
FROM globalmart.supply_chain.vendor_contract_chunks_index_source
ORDER BY source_file, chunk_index;
```

Explain that one row equals one retrieval chunk. The final row for a document can contain fewer than 500 tokens.

### Part D: Ask AI Search questions

Use the AI Search index to ask:

- What are the weather-delay rules for Gold Tier vendors in the Midwest?
- What is the delay penalty for Alpine Apparel Ltd?
- Which vendor has 100% financial liability during weather delays?
- What delivery requirements apply to Bronze Tier vendors in the West?

Use metadata filters where available:

- `vendor_id = VEND-789`
- `support_tier = Gold`
- `region_covered = Midwest`

## 9. Demo Close

End by explaining the division of responsibility:

- SQL provides exact, auditable numerical answers.
- AI Search provides grounded answers from contract language.
- Shared vendor and regional metadata connects the two experiences.
- Genie provides the managed SQL-first path; the Custom Agent and App provide
  explicit orchestration and evidence presentation.
- The triggered index makes refresh timing explicit and operationally visible.

## 10. Troubleshooting

- **Index is Online but results are stale:** run the refresh job, then trigger an index sync.
- **Source table is not accepted by AI Search:** use `vendor_contract_chunks_index_source`; the workspace does not accept the Streaming Table or Materialized View as an AI Search source.
- **No new chunks after replacing files:** run `refresh_vendor_contract_chunks`; it performs an incremental scan by default. Use `INGESTION_MODE=full_rebuild` when reconciling the complete Volume.
- **Python import error locally:** use `/opt/az/bin/python3` with the installed Databricks packages, or install `requirements-dev.txt` into a separate virtual environment.
