-- Restore the exact dim_vendors values captured by
-- 01_prepare_penalty_scenario_vendors.sql.
-- This restores only the five columns changed by that setup script.

MERGE INTO globalmart.supply_chain.dim_vendors AS target
USING globalmart.supply_chain.demo_penalty_vendor_backup AS source
ON target.vendor_id = source.vendor_id
WHEN MATCHED THEN UPDATE SET
  vendor_name = source.vendor_name,
  support_tier = source.support_tier,
  region_covered = source.region_covered,
  account_manager = source.account_manager;

SELECT assert_true(
  COUNT(*) = 3
    AND SUM(CASE
      WHEN target.vendor_id IS NULL
        OR NOT (
          target.vendor_name <=> source.vendor_name
          AND target.support_tier <=> source.support_tier
          AND target.region_covered <=> source.region_covered
          AND target.account_manager <=> source.account_manager
        )
      THEN 1 ELSE 0 END) = 0,
  'The vendor rows were not fully restored from the demo backup.'
)
FROM globalmart.supply_chain.demo_penalty_vendor_backup AS source
LEFT JOIN globalmart.supply_chain.dim_vendors AS target
  ON target.vendor_id = source.vendor_id;

SELECT
  vendor_id,
  vendor_name,
  support_tier,
  region_covered,
  account_manager
FROM globalmart.supply_chain.dim_vendors
WHERE vendor_id IN ('VEND-123', 'VEND-456', 'VEND-789')
ORDER BY vendor_id;

-- Drop the scoped backup only after the restoration assertion succeeds.
DROP TABLE globalmart.supply_chain.demo_penalty_vendor_backup;
