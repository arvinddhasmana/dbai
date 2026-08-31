# Contract Change Demo Runbook

This runbook demonstrates three file lifecycle events in the contract ingestion pipeline:

1. Add a contract for a new vendor.
2. Update an existing contract without changing its file identity.
3. Delete an existing contract and remove its searchable chunks.

The demo runner uploads files to the Unity Catalog Volume, invokes the deployed refresh job, and prints the Databricks CLI output. Run each action from the repository root.

## What The Demo Uses

| Purpose | Local path | Remote file name |
|---|---|---|
| Baseline contracts | `sample_data/vendor_contracts/` | `Contract_VEND123_Bronze.txt`, `Contract_VEND456_Silver.txt`, `Contract_VEND789_Gold.txt` |
| New vendor contract | `sample_data/vendor_contracts/demo/add/Contract_VEND321_Platinum.txt` | `Contract_VEND321_Platinum.txt` |
| Updated contract | `sample_data/vendor_contracts/demo/update/Contract_VEND456_Silver.txt` | `Contract_VEND456_Silver.txt` |

The updated local file intentionally uses the same remote name as the original Silver contract. This preserves the normalized path and `file_id`, so the refresh job classifies the change as `UPDATED` rather than `NEW`.

The new vendor is `VEND-321`, Northstar Cold Chain, Platinum tier, Southeast region. Its metadata is present in both the contract parser and `dim_vendors`.

## Prerequisites

The Databricks CLI, Python SDK, and a serverless SQL warehouse must be
available. Set the profile and warehouse ID:

```bash
export DATABRICKS_CONFIG_PROFILE=aiarchitect
export DATABRICKS_SQL_WAREHOUSE_ID=<serverless-sql-warehouse-id>
```

For a new or fully reset demo environment, run the idempotent bootstrap:

```bash
/opt/az/bin/python3 scripts/local/bootstrap_demo_environment.py
```

It creates the catalog/schema/Volume, deploys the three bundle jobs, rebuilds
all Delta demo tables from the baseline files, provisions the Search endpoint
and index, and creates the Genie function. It never triggers the `TRIGGERED`
Search sync. Manually sync the index once before running the Search checks in
Step 5.

## Step 1: Reset And Show Baseline

Reset uploads the three baseline files, removes the demo-only new vendor file, and runs a `full_rebuild`. It also restores the original Silver contract if the demo was run previously.

```bash
/opt/az/bin/python3 scripts/local/run_contract_change_demo.py reset
```

Show the three baseline files locally, then show the current Gold state in Databricks SQL:

```bash
find sample_data/vendor_contracts -maxdepth 1 -type f -printf '%f\n' | sort
```

```sql
SELECT
  source_file,
  vendor_id,
  support_tier,
  region_covered,
  document_version,
  COUNT(*) AS chunk_count
FROM globalmart.supply_chain.vendor_contract_chunks_index_source
GROUP BY source_file, vendor_id, support_tier, region_covered, document_version
ORDER BY source_file;
```

Show the current manifest:

```sql
SELECT
  source_file,
  lifecycle_status,
  document_version,
  last_event_type,
  last_seen_at
FROM globalmart.supply_chain.contract_file_manifest
ORDER BY source_file;
```

Expected baseline: three `ACTIVE` files, all at document version `1`, with event type `NEW`. There should be no `VEND-321` contract.

## Step 2: Add A New Vendor Contract

First inspect the file that will be added:

```bash
sed -n '1,24p' sample_data/vendor_contracts/demo/add/Contract_VEND321_Platinum.txt
```

Run the add action:

```bash
/opt/az/bin/python3 scripts/local/run_contract_change_demo.py add
```

The runner uploads the file as `/Volumes/globalmart/supply_chain/vendor_contracts/Contract_VEND321_Platinum.txt` and runs the refresh job in `incremental` mode.

Show the new Bronze event:

```sql
SELECT
  event_type,
  source_file,
  run_id,
  content_hash,
  observed_at
FROM globalmart.supply_chain.contract_file_events_bronze
WHERE source_file = 'Contract_VEND321_Platinum.txt'
ORDER BY observed_at DESC;
```

Show the new Silver document and its vendor association:

```sql
SELECT
  source_file,
  vendor_id,
  vendor_name,
  support_tier,
  region_covered,
  lifecycle_status,
  document_version
FROM globalmart.supply_chain.contract_documents_silver
WHERE source_file = 'Contract_VEND321_Platinum.txt';
```

```sql
SELECT *
FROM globalmart.supply_chain.dim_vendors
WHERE vendor_id = 'VEND-321';
```

Show the Gold chunks:

```sql
SELECT
  source_file,
  vendor_id,
  support_tier,
  region_covered,
  document_version,
  chunk_index,
  token_count,
  LEFT(chunk_text, 180) AS chunk_preview
FROM globalmart.supply_chain.vendor_contract_chunks_index_source
WHERE source_file = 'Contract_VEND321_Platinum.txt'
ORDER BY chunk_index;
```

What to call out: the event is `NEW`; the document is `ACTIVE`; the parser inferred `VEND-321` from the filename and attached Northstar, Platinum, and Southeast metadata; and Gold contains chunks for the new file.

## Step 3: Update The Existing Silver Contract

Inspect the amended local contract and the changed commercial terms:

```bash
grep -n -E 'UPDATED|97 percent|\$750|\$2,000' sample_data/vendor_contracts/demo/update/Contract_VEND456_Silver.txt
```

Run the update action:

```bash
/opt/az/bin/python3 scripts/local/run_contract_change_demo.py update
```

Show the event history for the unchanged file identity:

```sql
SELECT
  event_type,
  source_file,
  run_id,
  content_hash,
  observed_at
FROM globalmart.supply_chain.contract_file_events_bronze
WHERE source_file = 'Contract_VEND456_Silver.txt'
ORDER BY observed_at;
```

Show the new current version:

```sql
SELECT
  source_file,
  content_hash,
  document_version,
  lifecycle_status,
  last_event_type,
  processed_at
FROM globalmart.supply_chain.contract_file_manifest
WHERE source_file = 'Contract_VEND456_Silver.txt';
```

Confirm that the replacement Gold chunks contain the amended terms:

```sql
SELECT
  document_version,
  chunk_index,
  token_count,
  LEFT(chunk_text, 300) AS chunk_preview
FROM globalmart.supply_chain.vendor_contract_chunks_index_source
WHERE source_file = 'Contract_VEND456_Silver.txt'
ORDER BY chunk_index;
```

What to call out: the second event is `UPDATED`, the content hash changed, the file remains the same logical file, the document version advances from `1` to `2`, and old active chunks are replaced by chunks containing the amended penalty and weather-cap language.

## Step 4: Delete A Contract

The Bronze contract is deliberately deleted from the Volume while its local baseline file remains available for reset.

```bash
/opt/az/bin/python3 scripts/local/run_contract_change_demo.py delete
```

Show the deletion event:

```sql
SELECT
  event_type,
  source_file,
  run_id,
  observed_at,
  is_supported,
  error_message
FROM globalmart.supply_chain.contract_file_events_bronze
WHERE source_file = 'Contract_VEND123_Bronze.txt'
ORDER BY observed_at DESC;
```

Show the retained lifecycle records:

```sql
SELECT
  source_file,
  lifecycle_status,
  document_version,
  last_event_type,
  last_seen_at
FROM globalmart.supply_chain.contract_file_manifest
WHERE source_file = 'Contract_VEND123_Bronze.txt';
```

```sql
SELECT
  source_file,
  lifecycle_status,
  document_version,
  extraction_error
FROM globalmart.supply_chain.contract_documents_silver
WHERE source_file = 'Contract_VEND123_Bronze.txt';
```

Confirm that no active searchable chunks remain:

```sql
SELECT COUNT(*) AS active_chunk_count
FROM globalmart.supply_chain.vendor_contract_chunks_index_source
WHERE source_file = 'Contract_VEND123_Bronze.txt'
  AND is_active = true;
```

Expected result: `event_type = 'DELETED'`, manifest and document lifecycle `DELETED`, and `active_chunk_count = 0`. Bronze and Silver retain the evidence instead of physically deleting the audit rows.

## Step 5: Configure Genie, Synchronize, And Demonstrate Search

The managed index is `TRIGGERED`, so repeat the sync operation after each successful refresh, including after reset:

1. Open the AI Search endpoint `globalmart-supply-chain-search`.
2. Open index `globalmart.supply_chain.vendor_contract_chunks_index_rebuilt`.
3. Select **Sync** or **Trigger update**.
4. Wait for update status **Completed** and index status **Online**.

Add the three structured tables and
`globalmart.supply_chain.search_vendor_contracts` to the Genie space. Use the
following instructions in the Genie space configuration:

```text
Use SQL against dim_products, dim_vendors, and fact_inventory_status for exact
numeric answers, joins, totals, dates, inventory values, and account-manager
questions. Use search_vendor_contracts for vendor-contract language, penalties,
weather exceptions, service levels, liability, and delivery obligations.

For hybrid questions, use SQL for structured facts and
search_vendor_contracts for contract evidence, and separate those results.
Pass vendor_id, support_tier, and region when the question provides them.
Always cite source_file and summarize the retrieved chunk evidence. Never
invent contract terms or treat Bronze/Silver lifecycle history as searchable
contract content.

The Search index contains current active Gold chunks only. If the function
returns no rows after a deletion, say that no active searchable contract is
available and do not answer from the deleted contract's audit records.
```

The managed index is `TRIGGERED`, so repeat the sync operation after each
successful refresh:

1. Open the AI Search endpoint `globalmart-supply-chain-search`.
2. Open index `globalmart.supply_chain.vendor_contract_chunks_index_rebuilt`.
3. Select **Sync** or **Trigger update**.
4. Wait for update status **Completed** and index status **Online**.

After each sync, ask Genie the lifecycle question for that stage. Genie should
use `search_vendor_contracts` for these contract questions. It searches the
current indexed Gold chunks, not Bronze event history or Silver lifecycle rows.

### Verify The Add

Ask:

> Which new vendor contract was added, and what are its support tier and covered region?

Expected answer: Genie identifies `Contract_VEND321_Platinum.txt` for VEND-321,
Northstar Cold Chain, with Platinum support tier and Southeast region. The
contract is active and available for retrieval.

### Verify The Update

Ask:

> What are the updated delay fee and weather penalty cap for Alpine Apparel Ltd?

Expected answer: Genie cites the VEND-456 amendment and reports a fixed $750
per-container-per-day fee for supplier-controlled delays. For qualifying
Northeast winter weather, penalties are capped at $2,000 total per affected
shipment, with Alpine Apparel liable for the first $2,000. The current document
version is 2.

### Verify The Delete

Ask:

> What are the weather-delay rules in the Pacific Warehousing VEND-123 contract?

Expected answer: Genie reports that no active searchable contract is available
for VEND-123 after synchronization. It should not answer from the deleted
contract. The Bronze and Silver lifecycle tables still retain the deletion audit
record, but that audit state is not searchable contract content.

After the add sync, run the function smoke test and show a `VEND-321` result:

```sql
SELECT
  source_file,
  vendor_id,
  vendor_name,
  support_tier,
  region_covered,
  LEFT(chunk_text, 240) AS chunk_preview,
  score
FROM globalmart.supply_chain.search_vendor_contracts(
  'What are the temperature and hurricane delay rules?',
  'VEND-321',
  'Platinum',
  'Southeast'
)
ORDER BY score DESC;
```

After the update sync, search for the amended Silver terms:

```sql
SELECT
  source_file,
  document_version,
  LEFT(chunk_text, 240) AS chunk_preview,
  score
FROM globalmart.supply_chain.search_vendor_contracts(
  'What is the updated weather penalty cap for Alpine Apparel?',
  'VEND-456',
  'Silver',
  'Northeast'
)
ORDER BY score DESC;
```

After the delete sync, confirm that the deleted Bronze file is not returned:

```sql
SELECT COUNT(*) AS deleted_contract_results
FROM globalmart.supply_chain.search_vendor_contracts(
  'What is the weather penalty for Pacific Warehousing?',
  'VEND-123',
  'Bronze',
  'West'
);
```

Expected result after index synchronization: `deleted_contract_results = 0`.

## Repeat The Demo

To return to a clean baseline and run it again:

```bash
/opt/az/bin/python3 scripts/local/run_contract_change_demo.py reset
```

Use `--no-run` when you want to inspect the Volume mutation before starting the refresh job. For example:

```bash
/opt/az/bin/python3 scripts/local/run_contract_change_demo.py add --no-run
```

Run the normal incremental job manually only when using `--no-run`:

```bash
databricks bundle run refresh_vendor_contract_chunks -t dev
```

The runner changes only the managed demo filenames. It does not delete unrelated files from the Volume.

## Destroy The Demo Environment

The complete teardown removes the Search index and endpoint, all files and the
Volume, the Genie function, all Bronze/Silver/Gold demo tables, the custom App,
and the bundle-managed jobs. It uses Databricks workspace APIs and does not
require a SQL warehouse. Preview the object list without contacting Databricks:

```bash
/opt/az/bin/python3 scripts/local/destroy_demo_environment.py --dry-run
```

Run the deletion only after reviewing the list:

```bash
/opt/az/bin/python3 scripts/local/destroy_demo_environment.py --yes
```

The script retains the `globalmart` catalog and `supply_chain` schema. Add
`--keep-bundle-resources` to preserve the jobs or `--drop-schema` when the
schema is dedicated to this demo. The teardown does not trigger Search sync.
