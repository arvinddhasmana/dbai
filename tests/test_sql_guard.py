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
