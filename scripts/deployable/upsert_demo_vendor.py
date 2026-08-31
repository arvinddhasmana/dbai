# Databricks notebook source

import os

from pyspark.sql import functions as F


VENDOR_ID = os.getenv("DEMO_VENDOR_ID", "VEND-321")
VENDOR_NAME = os.getenv("DEMO_VENDOR_NAME", "Northstar Cold Chain")
SUPPORT_TIER = os.getenv("DEMO_SUPPORT_TIER", "Platinum")
REGION_COVERED = os.getenv("DEMO_REGION", "Southeast")
ACCOUNT_MANAGER = os.getenv("DEMO_ACCOUNT_MANAGER", "Avery Morgan")


def runtime_parameter(name, default):
  try:
    dbutils.widgets.text(name, os.getenv(name, default))
    return dbutils.widgets.get(name)
  except NameError:
    return os.getenv(name, default)


CATALOG = runtime_parameter("CATALOG", os.getenv("DBAI_CATALOG", "globalmart"))
VENDOR_TABLE = f"{CATALOG}.supply_chain.dim_vendors"


vendor = spark.createDataFrame(
    [(VENDOR_ID, VENDOR_NAME, SUPPORT_TIER, REGION_COVERED, ACCOUNT_MANAGER)],
    ["vendor_id", "vendor_name", "support_tier", "region_covered", "account_manager"],
)
vendor.createOrReplaceTempView("demo_vendor")
spark.sql(f"""
    MERGE INTO {VENDOR_TABLE} AS target
    USING demo_vendor AS source
      ON target.vendor_id = source.vendor_id
    WHEN MATCHED THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
""")
print(f"[CONTROL] Vendor dimension upserted: {VENDOR_ID}")
