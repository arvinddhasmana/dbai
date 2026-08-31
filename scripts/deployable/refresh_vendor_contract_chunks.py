# Databricks notebook source

from io import BytesIO
from pathlib import PurePosixPath
import hashlib
import os
import re
import uuid

from pyspark.sql import functions as F
from pyspark.sql import types as T
from delta.tables import DeltaTable


def runtime_parameter(name, default):
    try:
        dbutils.widgets.text(name, os.getenv(name, default))
        return dbutils.widgets.get(name)
    except NameError:
        return os.getenv(name, default)


# [LANDING], [BRONZE], [SILVER], and [GOLD] object locations.
CATALOG = runtime_parameter("CATALOG", os.getenv("DBAI_CATALOG", "globalmart"))
INPUT_PATH = f"/Volumes/{CATALOG}/supply_chain/vendor_contracts"
EVENTS_TABLE = f"{CATALOG}.supply_chain.contract_file_events_bronze"
MANIFEST_TABLE = f"{CATALOG}.supply_chain.contract_file_manifest"
DOCUMENTS_TABLE = f"{CATALOG}.supply_chain.contract_documents_silver"
TARGET_TABLE = f"{CATALOG}.supply_chain.vendor_contract_chunks_index_source"
SUPPORTED_EXTENSIONS = ("pdf", "txt", "md", "csv", "json", "html")


def runtime_parameter(name, default):
    try:
        dbutils.widgets.text(name, os.getenv(name, default))
        return dbutils.widgets.get(name)
    except NameError:
        return os.getenv(name, default)


INGESTION_MODE = runtime_parameter("INGESTION_MODE", "incremental").lower()

VENDOR_METADATA = {
    "VEND-789": ("Logistics & Electronics Corp", "Gold", "Midwest"),
    "VEND-456": ("Alpine Apparel Ltd", "Silver", "Northeast"),
    "VEND-123": ("Pacific Warehousing", "Bronze", "West"),
    "VEND-321": ("Northstar Cold Chain", "Platinum", "Southeast"),
}

CHUNK_FIELDS = [
    "chunk_id", "file_id", "content_hash", "document_version", "is_active",
    "processed_at", "source_path", "source_file", "source_modified_at",
    "source_size_bytes", "vendor_id", "vendor_name", "support_tier",
    "region_covered", "chunk_index", "chunk_text", "token_count",
]
CHUNK_SCHEMA = T.StructType([
    T.StructField("chunk_id", T.StringType(), False),
    T.StructField("file_id", T.StringType(), False),
    T.StructField("content_hash", T.StringType(), False),
    T.StructField("document_version", T.LongType(), False),
    T.StructField("is_active", T.BooleanType(), False),
    T.StructField("processed_at", T.TimestampType(), True),
    T.StructField("source_path", T.StringType(), False),
    T.StructField("source_file", T.StringType(), False),
    T.StructField("source_modified_at", T.TimestampType(), True),
    T.StructField("source_size_bytes", T.LongType(), True),
    T.StructField("vendor_id", T.StringType(), False),
    T.StructField("vendor_name", T.StringType(), False),
    T.StructField("support_tier", T.StringType(), False),
    T.StructField("region_covered", T.StringType(), False),
    T.StructField("chunk_index", T.IntegerType(), False),
    T.StructField("chunk_text", T.StringType(), False),
    T.StructField("token_count", T.IntegerType(), False),
])


def normalize_path(path):
    return str(PurePosixPath(path))


def stable_file_id(path):
    return hashlib.sha256(normalize_path(path).encode("utf-8")).hexdigest()


def event_id(file_id, content_hash, event_type):
    return hashlib.sha256(
        f"{file_id}:{content_hash or ''}:{event_type}".encode("utf-8")
    ).hexdigest()


def classify_file(previous_hash, current_hash, is_present=True):
    if not is_present:
        return "DELETED"
    if previous_hash is None:
        return "NEW"
    if previous_hash != current_hash:
        return "UPDATED"
    return "UNCHANGED"


def extract_text(path, content):
    if PurePosixPath(path).suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    return content.decode("utf-8", errors="replace")


def normalize_text(text):
    return " ".join(text.split())


def chunk_text(text):
    import tiktoken

    encoder = tiktoken.get_encoding("cl100k_base")
    tokens = encoder.encode(normalize_text(text), disallowed_special=())
    return [
        encoder.decode(tokens[start : start + 500]).strip()
        for start in range(0, len(tokens), 450)
        if tokens[start : start + 500]
    ]


def parse_documents(batches):
    import pandas as pd

    for batch in batches:
        rows = []
        for row in batch.itertuples(index=False):
            vendor_match = re.search(r"VEND[-_]?(\d+)", row.path.upper())
            vendor_id = f"VEND-{vendor_match.group(1)}" if vendor_match else "UNKNOWN"
            vendor_name, tier, region = VENDOR_METADATA.get(
                vendor_id, ("Unknown vendor", "Unknown", "Unknown")
            )
            try:
                rows.append({
                    "file_id": row.file_id,
                    "source_path": row.path,
                    "source_file": PurePosixPath(row.path).name,
                    "content_hash": row.content_hash,
                    "document_version": row.document_version,
                    "normalized_text": normalize_text(extract_text(row.path, row.content)),
                    "vendor_id": vendor_id,
                    "vendor_name": vendor_name,
                    "support_tier": tier,
                    "region_covered": region,
                    "lifecycle_status": "ACTIVE",
                    "extracted_at": row.processed_at,
                    "extraction_error": None,
                })
            except Exception as error:
                rows.append({
                    "file_id": row.file_id, "source_path": row.path,
                    "source_file": PurePosixPath(row.path).name,
                    "content_hash": row.content_hash,
                    "document_version": row.document_version,
                    "normalized_text": None, "vendor_id": "UNKNOWN",
                    "vendor_name": "Unknown vendor", "support_tier": "Unknown",
                    "region_covered": "Unknown", "lifecycle_status": "FAILED",
                    "extracted_at": row.processed_at,
                    "extraction_error": str(error)[:4000],
                })
        yield pd.DataFrame(rows)


def parse_chunks(batches):
    import pandas as pd
    import tiktoken

    encoder = tiktoken.get_encoding("cl100k_base")
    for batch in batches:
        rows = []
        for row in batch.itertuples(index=False):
            for index, text_chunk in enumerate(chunk_text(row.normalized_text)):
                rows.append({
                    "chunk_id": hashlib.sha256(
                        f"{row.file_id}:{row.document_version}:{index}:{text_chunk}".encode("utf-8")
                    ).hexdigest(),
                    "file_id": row.file_id, "content_hash": row.content_hash,
                    "document_version": row.document_version, "is_active": True,
                    "processed_at": row.extracted_at, "source_path": row.source_path,
                    "source_file": row.source_file, "source_modified_at": row.source_modified_at,
                    "source_size_bytes": row.source_size_bytes, "vendor_id": row.vendor_id,
                    "vendor_name": row.vendor_name, "support_tier": row.support_tier,
                    "region_covered": row.region_covered, "chunk_index": index,
                    "chunk_text": text_chunk,
                    "token_count": len(encoder.encode(text_chunk, disallowed_special=())),
                })
        yield pd.DataFrame(rows)


def ensure_table(df, table_name, cdf=True):
    if not spark.catalog.tableExists(table_name):
        writer = df.limit(0).write.format("delta").mode("overwrite")
        if cdf:
            writer = writer.option("delta.enableChangeDataFeed", "true")
        writer.saveAsTable(table_name)


def merge_events(events):
    events.createOrReplaceTempView("contract_events_batch")
    DeltaTable.forName(spark, EVENTS_TABLE).alias("target").merge(
        events.alias("source"), "target.event_id = source.event_id"
    ).whenNotMatchedInsertAll().execute()


def merge_manifest(manifest):
    manifest.createOrReplaceTempView("contract_manifest_batch")
    DeltaTable.forName(spark, MANIFEST_TABLE).alias("target").merge(
        manifest.alias("source"), "target.file_id = source.file_id"
    ).whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()


def merge_documents(documents):
    DeltaTable.forName(spark, DOCUMENTS_TABLE).alias("target").merge(
        documents.alias("source"), "target.file_id = source.file_id"
    ).whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()


def merge_gold(chunks, affected_file_ids):
    if not affected_file_ids:
        return 0
    prepared = chunks.select(*CHUNK_FIELDS)
    replacement_count = prepared.count()
    DeltaTable.forName(spark, TARGET_TABLE).delete(
        F.col("file_id").isin(affected_file_ids)
    )
    if replacement_count:
        prepared.write.format("delta").mode("append").saveAsTable(TARGET_TABLE)
    return replacement_count


def ensure_gold_schema():
    if not spark.catalog.tableExists(TARGET_TABLE):
        spark.createDataFrame([], CHUNK_SCHEMA).write.format("delta").option(
            "delta.enableChangeDataFeed", "true"
        ).saveAsTable(TARGET_TABLE)
        return
    existing_columns = {field.name for field in spark.table(TARGET_TABLE).schema.fields}
    additions = {
        "file_id": "STRING",
        "content_hash": "STRING",
        "document_version": "BIGINT",
        "is_active": "BOOLEAN",
        "processed_at": "TIMESTAMP",
    }
    for column, data_type in additions.items():
        if column not in existing_columns:
            spark.sql(f"ALTER TABLE {TARGET_TABLE} ADD COLUMNS ({column} {data_type})")
    spark.sql(f"ALTER TABLE {TARGET_TABLE} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
    spark.sql(f"UPDATE {TARGET_TABLE} SET is_active = true WHERE is_active IS NULL")


def full_rebuild(raw_files, processed_at):
    all_files = raw_files.withColumn("document_version", F.lit(1).cast("long")).withColumn(
        "processed_at", processed_at.cast("timestamp")
    )
    documents = all_files.mapInPandas(parse_documents, schema=document_schema)
    active_documents = documents.where(F.col("lifecycle_status") == "ACTIVE")
    chunk_input = active_documents.join(
        all_files.select("file_id", "source_modified_at", "source_size_bytes"), "file_id"
    )
    chunks = chunk_input.mapInPandas(parse_chunks, schema=CHUNK_SCHEMA)
    chunks.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).option("delta.enableChangeDataFeed", "true").saveAsTable(TARGET_TABLE)
    documents.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).option("delta.enableChangeDataFeed", "true").saveAsTable(DOCUMENTS_TABLE)
    manifest = all_files.select(
        "file_id", "source_path", F.element_at(F.split("source_path", "/"), -1).alias("source_file"),
        "content_hash", "source_modified_at", "source_size_bytes"
    ).withColumn("document_version", F.lit(1).cast("long")).withColumn(
        "lifecycle_status", F.lit("ACTIVE")
    ).withColumn("last_event_type", F.lit("NEW")).withColumn("last_run_id", F.lit(run_id)).withColumn(
        "first_seen_at", processed_at
    ).withColumn("last_seen_at", processed_at).withColumn("processed_at", processed_at).withColumn(
        "processing_error", F.lit(None).cast("string")
    )
    manifest.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).option("delta.enableChangeDataFeed", "true").saveAsTable(MANIFEST_TABLE)
    events = manifest.select(
        F.sha2(F.concat_ws(":", "file_id", "content_hash", F.lit("NEW")), 256).alias("event_id"),
        F.lit(run_id).alias("run_id"), processed_at.alias("observed_at"), "file_id", "source_path",
        "source_file", F.lit("NEW").alias("event_type"), "content_hash", "source_modified_at",
        "source_size_bytes", F.lit(True).alias("is_supported"), F.lit(None).cast("string").alias("error_message")
    )
    events.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).option("delta.enableChangeDataFeed", "true").saveAsTable(EVENTS_TABLE)
    return documents, chunks


manifest_schema = T.StructType([
    T.StructField("file_id", T.StringType(), False), T.StructField("source_path", T.StringType(), False),
    T.StructField("source_file", T.StringType(), False), T.StructField("content_hash", T.StringType(), True),
    T.StructField("source_modified_at", T.TimestampType(), True), T.StructField("source_size_bytes", T.LongType(), True),
    T.StructField("document_version", T.LongType(), False), T.StructField("lifecycle_status", T.StringType(), False),
    T.StructField("last_event_type", T.StringType(), False), T.StructField("last_run_id", T.StringType(), False),
    T.StructField("first_seen_at", T.TimestampType(), True), T.StructField("last_seen_at", T.TimestampType(), True),
    T.StructField("processed_at", T.TimestampType(), True), T.StructField("processing_error", T.StringType(), True),
])
document_schema = T.StructType([
    T.StructField("file_id", T.StringType(), False), T.StructField("source_path", T.StringType(), False),
    T.StructField("source_file", T.StringType(), False), T.StructField("content_hash", T.StringType(), False),
    T.StructField("document_version", T.LongType(), False), T.StructField("normalized_text", T.StringType(), True),
    T.StructField("vendor_id", T.StringType(), False), T.StructField("vendor_name", T.StringType(), False),
    T.StructField("support_tier", T.StringType(), False), T.StructField("region_covered", T.StringType(), False),
    T.StructField("lifecycle_status", T.StringType(), False), T.StructField("extracted_at", T.TimestampType(), True),
    T.StructField("extraction_error", T.StringType(), True),
])
event_schema = T.StructType([
    T.StructField("event_id", T.StringType(), False), T.StructField("run_id", T.StringType(), False),
    T.StructField("observed_at", T.TimestampType(), True), T.StructField("file_id", T.StringType(), False),
    T.StructField("source_path", T.StringType(), False), T.StructField("source_file", T.StringType(), False),
    T.StructField("event_type", T.StringType(), False), T.StructField("content_hash", T.StringType(), True),
    T.StructField("source_modified_at", T.TimestampType(), True), T.StructField("source_size_bytes", T.LongType(), True),
    T.StructField("is_supported", T.BooleanType(), False), T.StructField("error_message", T.StringType(), True),
])


processed_at = F.current_timestamp()
run_id = os.getenv("INGESTION_RUN_ID", str(uuid.uuid4()))
raw_files = (
    spark.read.format("binaryFile").load(INPUT_PATH)
    .where(F.lower(F.element_at(F.split(F.col("path"), "\\."), -1)).isin(*SUPPORTED_EXTENSIONS))
    .select(
        "path", "content", "modificationTime", "length",
        F.col("path").alias("source_path"),
        F.col("modificationTime").alias("source_modified_at"),
        F.col("length").alias("source_size_bytes"),
    )
    .withColumn("file_id", F.sha2(F.col("path"), 256))
    .withColumn("content_hash", F.sha2(F.col("content"), 256))
    .withColumn("processed_at", processed_at)
)

ensure_table(spark.createDataFrame([], event_schema), EVENTS_TABLE)
ensure_table(spark.createDataFrame([], manifest_schema), MANIFEST_TABLE)
ensure_table(spark.createDataFrame([], document_schema), DOCUMENTS_TABLE)
if INGESTION_MODE != "full_rebuild":
    ensure_gold_schema()

if INGESTION_MODE == "full_rebuild":
    full_rebuild(raw_files, processed_at)
    print(f"[CONTROL] Full rebuild completed for [GOLD] {TARGET_TABLE}")
else:
    all_manifest = spark.table(MANIFEST_TABLE)
    current_manifest = all_manifest.where(F.col("lifecycle_status") == "ACTIVE")
    observed = raw_files.alias("current").join(
        current_manifest.select(
            F.col("file_id").alias("previous_file_id"), F.col("content_hash").alias("previous_hash"),
            F.col("document_version").alias("previous_version")
        ).alias("previous"),
        F.col("current.file_id") == F.col("previous.previous_file_id"), "left"
    ).select("current.*", "previous_hash", "previous_version").withColumn(
        "event_type", F.when(F.col("previous_hash").isNull(), "NEW")
        .when(F.col("previous_hash") != F.col("content_hash"), "UPDATED")
        .otherwise("UNCHANGED")
    )
    missing = current_manifest.join(raw_files.select("file_id"), "file_id", "leftanti").select(
        "file_id", "source_path", "source_file", "content_hash", "source_modified_at", "source_size_bytes",
        F.lit("DELETED").alias("event_type"), F.lit(run_id).alias("run_id"), processed_at.alias("observed_at"),
        F.sha2(F.concat_ws(":", "file_id", "content_hash", F.lit("DELETED")), 256).alias("event_id"),
        F.lit(False).alias("is_supported"), F.lit(None).cast("string").alias("error_message")
    ).select(event_schema.fieldNames())
    actionable = observed.where(F.col("event_type") != "UNCHANGED")
    gold_state = spark.table(TARGET_TABLE).where(F.col("is_active")).groupBy("file_id").agg(
        F.max("document_version").alias("gold_version"),
        F.max("content_hash").alias("gold_hash"),
    )
    missing_gold = observed.where(F.col("event_type") == "UNCHANGED").join(
        gold_state, "file_id", "left"
    ).where(
        F.col("gold_version").isNull()
        | (F.col("gold_version") != F.col("previous_version"))
        | (F.col("gold_hash") != F.col("content_hash"))
    ).withColumn("event_type", F.lit("REPAIRED"))
    actionable = actionable.unionByName(missing_gold, allowMissingColumns=True)
    orphaned_gold = all_manifest.where(F.col("lifecycle_status") != "ACTIVE").join(
        gold_state.select("file_id"), "file_id", "inner"
    ).select("file_id").distinct()
    events = actionable.select(
        F.sha2(F.concat_ws(":", "file_id", "content_hash", "event_type"), 256).alias("event_id"),
        F.lit(run_id).alias("run_id"), processed_at.alias("observed_at"), "file_id", "path",
        F.element_at(F.split("path", "/"), -1),
        "event_type", "content_hash", "source_modified_at", "source_size_bytes", F.lit(True).alias("is_supported"),
        F.lit(None).cast("string").alias("error_message")
    ).toDF(*event_schema.fieldNames()).unionByName(missing)
    merge_events(events)

    changed = actionable.withColumn(
        "document_version",
        F.when(F.col("event_type") == "REPAIRED", F.col("previous_version"))
        .otherwise(F.coalesce(F.col("previous_version") + 1, F.lit(1)))
        .cast("long")
    )
    documents = changed.mapInPandas(parse_documents, schema=document_schema)
    valid_documents = documents.where(F.col("lifecycle_status") == "ACTIVE")
    deleted_documents = missing.select(
        "file_id", "source_path", "source_file", "content_hash"
    ).withColumn("document_version", F.lit(0).cast("long")).withColumn(
        "normalized_text", F.lit(None).cast("string")
    ).withColumn("vendor_id", F.lit("UNKNOWN")).withColumn(
        "vendor_name", F.lit("Unknown vendor")
    ).withColumn("support_tier", F.lit("Unknown")).withColumn(
        "region_covered", F.lit("Unknown")
    ).withColumn("lifecycle_status", F.lit("DELETED")).withColumn(
        "extracted_at", F.lit(None).cast("timestamp")
    ).withColumn("extraction_error", F.lit(None).cast("string"))
    merge_documents(documents.unionByName(deleted_documents))
    manifest_updates = changed.select(
        "file_id", "source_path", F.element_at(F.split("source_path", "/"), -1), "content_hash",
        "source_modified_at", "source_size_bytes", "document_version", "event_type"
    ).toDF("file_id", "source_path", "source_file", "content_hash", "source_modified_at", "source_size_bytes", "document_version", "last_event_type").withColumn(
        "lifecycle_status", F.lit("ACTIVE")
    ).withColumn("last_run_id", F.lit(run_id)).withColumn("first_seen_at", processed_at).withColumn(
        "last_seen_at", processed_at
    ).withColumn("processed_at", processed_at).withColumn("processing_error", F.lit(None).cast("string"))
    deletion_manifest = missing.select(
        "file_id", "source_path", "source_file", "content_hash", "source_modified_at", "source_size_bytes"
    ).withColumn("document_version", F.lit(0).cast("long")).withColumn(
        "lifecycle_status", F.lit("DELETED")
    ).withColumn("last_event_type", F.lit("DELETED")).withColumn("last_run_id", F.lit(run_id)).withColumn(
        "first_seen_at", F.lit(None).cast("timestamp")
    ).withColumn("last_seen_at", processed_at).withColumn("processed_at", processed_at).withColumn(
        "processing_error", F.lit(None).cast("string")
    )
    merge_manifest(manifest_updates.unionByName(deletion_manifest))
    changed_chunks = valid_documents.join(changed.select("file_id", "source_modified_at", "source_size_bytes"), "file_id").mapInPandas(parse_chunks, schema=CHUNK_SCHEMA)
    affected_file_ids = actionable.select("file_id").unionByName(
        missing.select("file_id")
    ).unionByName(
        orphaned_gold
    ).distinct()
    changed_chunk_count = merge_gold(
        changed_chunks, [row.file_id for row in affected_file_ids.collect()]
    )
    print(f"[CONTROL] Incremental run {run_id}: {actionable.count()} affected files; {changed_chunk_count} replacement chunks; [GOLD] updated")
