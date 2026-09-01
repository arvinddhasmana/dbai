import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "app"))

from agent_server.sql_guard import validate_read_only_sql


def test_adds_default_limit_to_allowed_query():
    statement = validate_read_only_sql(
        "SELECT vendor_id FROM globalmart.supply_chain.dim_vendors"
    )
    assert statement.endswith("LIMIT 100")


def test_accepts_qualified_backtick_identifiers():
    statement = validate_read_only_sql(
        "SELECT * FROM `globalmart`.`supply_chain`.`dim_vendors` LIMIT 10"
    )
    assert "LIMIT 10" in statement


def test_qualifies_short_governed_table_names():
    statement = validate_read_only_sql(
        "SELECT category, COUNT(*) FROM dim_products GROUP BY category"
    )
    assert "FROM globalmart.supply_chain.dim_products" in statement


def test_qualifies_schema_only_governed_table_names():
    statement = validate_read_only_sql(
        "SELECT vendor_id FROM supply_chain.dim_vendors"
    )
    assert "FROM globalmart.supply_chain.dim_vendors" in statement


def test_accepts_monthly_aggregation_with_governed_joins():
    statement = validate_read_only_sql(
        """
        WITH monthly_inventory AS (
            SELECT date_trunc('MONTH', f.log_date) AS month,
                   SUM(f.stock_on_hand) AS total_stock_on_hand
            FROM globalmart.supply_chain.fact_inventory_status f
            JOIN globalmart.supply_chain.dim_products p
              ON f.product_id = p.product_id
            GROUP BY date_trunc('MONTH', f.log_date)
        )
        SELECT month, total_stock_on_hand
        FROM monthly_inventory
        ORDER BY month
        """
    )
    assert "LIMIT 100" in statement


def test_accepts_governed_contract_tables():
    statement = validate_read_only_sql(
        "SELECT source_file FROM globalmart.supply_chain.vendor_contract_chunks_index_rebuilt"
    )
    assert "LIMIT 100" in statement


def test_rejects_write_operations():
    for statement in (
        "DELETE FROM globalmart.supply_chain.dim_vendors",
        "UPDATE globalmart.supply_chain.dim_vendors SET vendor_id = 'x'",
        "DROP TABLE globalmart.supply_chain.dim_vendors",
    ):
        try:
            validate_read_only_sql(statement)
        except ValueError:
            continue
        raise AssertionError("write operation was accepted")


def test_rejects_unapproved_tables_and_large_limits():
    for statement in (
        "SELECT * FROM system.information_schema.tables",
        "SELECT * FROM globalmart.supply_chain.dim_vendors LIMIT 101",
    ):
        try:
            validate_read_only_sql(statement)
        except ValueError:
            continue
        raise AssertionError("unsafe query was accepted")


def test_product_name_matching_accepts_singular_and_plural_forms():
    import sys

    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parents[1] / "app"))
    from agent_server.product_matching import normalize_product_name

    assert normalize_product_name("Thermal Winter Coat") == "thermal winter coat"
    assert normalize_product_name("Thermal Winter Coats") == "thermal winter coat"
