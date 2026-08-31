-- [SERVING] Genie-facing contract search function over the [SERVING] AI Search index.
-- Run this script in a serverless SQL warehouse with USE CATALOG/SCHEMA and
-- CREATE FUNCTION privileges on globalmart.supply_chain.
--
-- vector_search() is currently a Public Preview SQL function. Its preview
-- interface does not accept filters_json or columns, so the optional business
-- filters are applied to the bounded result set after retrieval. Keep the
-- result limit conservative and validate recall with representative questions.

CREATE OR REPLACE FUNCTION globalmart.supply_chain.search_vendor_contracts(
  search_text STRING,
  requested_vendor_id STRING DEFAULT NULL,
  requested_support_tier STRING DEFAULT NULL,
  requested_region STRING DEFAULT NULL
)
RETURNS TABLE (
  chunk_id STRING,
  source_path STRING,
  source_file STRING,
  source_modified_at TIMESTAMP,
  source_size_bytes BIGINT,
  vendor_id STRING,
  vendor_name STRING,
  support_tier STRING,
  region_covered STRING,
  chunk_index INT,
  chunk_text STRING,
  token_count INT,
  score DOUBLE
)
RETURN
  SELECT
    chunk_id,
    source_path,
    source_file,
    source_modified_at,
    source_size_bytes,
    vendor_id,
    vendor_name,
    support_tier,
    region_covered,
    chunk_index,
    chunk_text,
    token_count,
    search_score AS score
  FROM vector_search(
    index => 'globalmart.supply_chain.vendor_contract_chunks_index_rebuilt',
    query_text => search_text,
    query_type => 'HYBRID',
    num_results => 10
  )
  WHERE (requested_vendor_id IS NULL OR vendor_id = requested_vendor_id)
    AND (
      requested_support_tier IS NULL
      OR support_tier = requested_support_tier
    )
    AND (requested_region IS NULL OR region_covered = requested_region);

COMMENT ON TABLE globalmart.supply_chain.dim_products IS
  '[GOLD] Product dimension. Join fact_inventory_status.product_id to product_id for product names, SKUs, categories, and unit costs.';
COMMENT ON TABLE globalmart.supply_chain.dim_vendors IS
  '[GOLD] Vendor dimension. Join fact_inventory_status.vendor_id to vendor_id for vendor names, support tiers, regions, and account managers.';
COMMENT ON TABLE globalmart.supply_chain.fact_inventory_status IS
  '[GOLD] Daily inventory status by product, vendor, and warehouse. Use stock_on_hand and unit_cost_usd to calculate inventory value.';
COMMENT ON TABLE globalmart.supply_chain.vendor_contract_chunks_index_source IS
  '[GOLD] Batch-refreshed contract chunks. Use the managed AI Search index through search_vendor_contracts for semantic and hybrid retrieval.';

ALTER TABLE globalmart.supply_chain.fact_inventory_status
  ALTER COLUMN transit_status COMMENT 'Operational status such as Delayed in Transit or On Time.';
ALTER TABLE globalmart.supply_chain.fact_inventory_status
  ALTER COLUMN units_in_transit COMMENT 'Units currently moving between supply-chain locations.';
ALTER TABLE globalmart.supply_chain.dim_vendors
  ALTER COLUMN account_manager COMMENT 'GlobalMart owner responsible for the vendor relationship.';
ALTER TABLE globalmart.supply_chain.vendor_contract_chunks_index_source
  ALTER COLUMN chunk_text COMMENT 'Contract text used for AI Search retrieval and evidence.';