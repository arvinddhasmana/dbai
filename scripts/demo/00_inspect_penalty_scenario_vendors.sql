-- Read-only preflight for the attached contract scenarios.
-- Expected vendor records are seeded by scripts/deployable/generate_mock_data.py.

SELECT
  vendor_id,
  vendor_name,
  support_tier,
  region_covered,
  account_manager
FROM globalmart.supply_chain.dim_vendors
WHERE vendor_id IN ('VEND-123', 'VEND-456', 'VEND-789')
ORDER BY vendor_id;

-- Optional context: current inventory rows for the three contract vendors.
SELECT
  vendor_id,
  transit_status,
  COUNT(*) AS inventory_rows,
  SUM(units_in_transit) AS units_in_transit
FROM globalmart.supply_chain.fact_inventory_status
WHERE vendor_id IN ('VEND-123', 'VEND-456', 'VEND-789')
GROUP BY vendor_id, transit_status
ORDER BY vendor_id, transit_status;
