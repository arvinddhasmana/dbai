# Databricks notebook source

import os

from pyspark.sql import functions as F
from pyspark.sql import types as T
from decimal import Decimal


def runtime_parameter(name, default):
    try:
        dbutils.widgets.text(name, os.getenv(name, default))
        return dbutils.widgets.get(name)
    except NameError:
        return os.getenv(name, default)


CATALOG = runtime_parameter("CATALOG", os.getenv("DBAI_CATALOG", "globalmart"))
SCHEMA = "supply_chain"


def main():
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")

    products = spark.createDataFrame(
        [
            ("P-101", "EcoFlow Battery Pack", "BATT-MW-01", "Electronics", Decimal("250.00")),
            ("P-102", "Quantum Smart TV", "TV-MW-99", "Electronics", Decimal("400.00")),
            ("P-103", "Thermal Winter Coats", "AP-NE-45", "Apparel", Decimal("45.00")),
            ("P-104", "HydroFlask 32oz", "HG-W-12", "Home Goods", Decimal("15.00")),
            ("P-105", "NeoGlow LED Strip", "LED-MW-05", "Electronics", Decimal("12.00")),
        ],
        T.StructType(
            [
                T.StructField("product_id", T.StringType(), False),
                T.StructField("product_name", T.StringType(), False),
                T.StructField("SKU", T.StringType(), False),
                T.StructField("category", T.StringType(), False),
                T.StructField("unit_cost_usd", T.DecimalType(10, 2), False),
            ]
        ),
    )

    vendors = spark.createDataFrame(
        [
            ("VEND-789", "Logistics & Electronics Corp", "Gold", "Midwest", "Sarah Jenkins"),
            ("VEND-456", "Alpine Apparel Ltd", "Silver", "Northeast", "Michael Chang"),
            ("VEND-123", "Pacific Warehousing", "Bronze", "West", "David Ross"),
            ("VEND-321", "Northstar Cold Chain", "Platinum", "Southeast", "Avery Morgan"),
        ],
        ["vendor_id", "vendor_name", "support_tier", "region_covered", "account_manager"],
    )

    inventory = spark.createDataFrame(
        [
            ("2026-08-25", "P-101", "VEND-789", "Chicago-Hub", 50, 1200, "Delayed in Transit"),
            ("2026-08-25", "P-102", "VEND-789", "Chicago-Hub", 150, 0, "On Time"),
            ("2026-08-25", "P-103", "VEND-456", "Boston-Hub", 450, 500, "On Time"),
            ("2026-08-25", "P-104", "VEND-123", "Seattle-Hub", 800, 0, "On Time"),
            ("2026-08-25", "P-105", "VEND-789", "Chicago-Hub", 0, 800, "Delayed in Transit"),
        ],
        [
            "log_date", "product_id", "vendor_id", "warehouse_location",
            "stock_on_hand", "units_in_transit", "transit_status",
        ],
    ).withColumn("log_date", F.to_date("log_date"))

    products.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{SCHEMA}.dim_products")
    vendors.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{SCHEMA}.dim_vendors")
    inventory.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{SCHEMA}.fact_inventory_status")


if __name__ == "__main__":
    main()