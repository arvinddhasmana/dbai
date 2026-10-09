-- Restore the three contract vendors to the metadata represented in their
-- attached contract files. This changes only dim_vendors; it does not modify
-- contract documents, the contract chunk table, or the AI Search index.
--
-- Run in a non-production workspace. Keep this backup table until the reset
-- script has completed successfully.

SELECT assert_true(
  COUNT(*) = 3 AND COUNT(DISTINCT vendor_id) = 3,
  'Expected exactly one dim_vendors row for each of VEND-123, VEND-456, and VEND-789.'
)
FROM globalmart.supply_chain.dim_vendors
WHERE vendor_id IN ('VEND-123', 'VEND-456', 'VEND-789');

-- Capture the current values once. IF NOT EXISTS preserves the original
-- snapshot if this setup script is rerun before the reset script.
CREATE TABLE IF NOT EXISTS
  globalmart.supply_chain.demo_penalty_vendor_backup
USING DELTA
AS
SELECT
  vendor_id,
  vendor_name,
  support_tier,
  region_covered,
  account_manager
FROM globalmart.supply_chain.dim_vendors
WHERE vendor_id IN ('VEND-123', 'VEND-456', 'VEND-789');

UPDATE globalmart.supply_chain.dim_vendors
SET
  vendor_name = CASE vendor_id
    WHEN 'VEND-123' THEN 'Pacific Warehousing'
    WHEN 'VEND-456' THEN 'Alpine Apparel Ltd'
    WHEN 'VEND-789' THEN 'Logistics & Electronics Corp'
    ELSE vendor_name
  END,
  support_tier = CASE vendor_id
    WHEN 'VEND-123' THEN 'Bronze'
    WHEN 'VEND-456' THEN 'Silver'
    WHEN 'VEND-789' THEN 'Gold'
    ELSE support_tier
  END,
  region_covered = CASE vendor_id
    WHEN 'VEND-123' THEN 'West'
    WHEN 'VEND-456' THEN 'Northeast'
    WHEN 'VEND-789' THEN 'Midwest'
    ELSE region_covered
  END,
  account_manager = CASE vendor_id
    WHEN 'VEND-123' THEN 'David Ross'
    WHEN 'VEND-456' THEN 'Michael Chang'
    WHEN 'VEND-789' THEN 'Sarah Jenkins'
    ELSE account_manager
  END
WHERE vendor_id IN ('VEND-123', 'VEND-456', 'VEND-789');

SELECT assert_true(
  COUNT(*) = 3
    AND SUM(CASE
      WHEN vendor_id = 'VEND-123'
        AND vendor_name = 'Pacific Warehousing'
        AND support_tier = 'Bronze'
        AND region_covered = 'West'
        AND account_manager = 'David Ross'
      THEN 1 ELSE 0 END) = 1
    AND SUM(CASE
      WHEN vendor_id = 'VEND-456'
        AND vendor_name = 'Alpine Apparel Ltd'
        AND support_tier = 'Silver'
        AND region_covered = 'Northeast'
        AND account_manager = 'Michael Chang'
      THEN 1 ELSE 0 END) = 1
    AND SUM(CASE
      WHEN vendor_id = 'VEND-789'
        AND vendor_name = 'Logistics & Electronics Corp'
        AND support_tier = 'Gold'
        AND region_covered = 'Midwest'
        AND account_manager = 'Sarah Jenkins'
      THEN 1 ELSE 0 END) = 1,
  'Vendor fixture values do not match the attached contracts.'
)
FROM globalmart.supply_chain.dim_vendors
WHERE vendor_id IN ('VEND-123', 'VEND-456', 'VEND-789');

SELECT
  vendor_id,
  vendor_name,
  support_tier,
  region_covered,
  account_manager
FROM globalmart.supply_chain.dim_vendors
WHERE vendor_id IN ('VEND-123', 'VEND-456', 'VEND-789')
ORDER BY vendor_id;
