-- Run these read-only checks after creating the function and synchronizing AI Search.

SELECT
  source_file,
  vendor_id,
  support_tier,
  region_covered,
  chunk_index,
  score,
  LEFT(chunk_text, 240) AS chunk_preview
FROM globalmart.supply_chain.search_vendor_contracts(
    'What are the weather delay rules and penalties?',
    NULL,
    NULL,
    NULL
  )
ORDER BY score DESC;

SELECT
  source_file,
  vendor_id,
  support_tier,
  region_covered,
  chunk_index,
  score,
  LEFT(chunk_text, 240) AS chunk_preview
FROM globalmart.supply_chain.search_vendor_contracts(
    'What happens during severe winter weather?',
    'VEND-789',
    'Gold',
    'Midwest'
  )
ORDER BY score DESC;

-- Expected: zero rows for a filter combination that does not exist.
SELECT COUNT(*) AS no_match_count
FROM globalmart.supply_chain.search_vendor_contracts(
    'delivery requirements',
    'VEND-789',
    'Silver',
    'Northeast'
  );