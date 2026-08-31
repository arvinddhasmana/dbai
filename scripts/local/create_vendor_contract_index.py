from databricks.ai_search.exceptions import NotFound

from demo_environment import ENDPOINT_NAME, INDEX_NAME, SOURCE_TABLE, create_search_client


EMBEDDING_ENDPOINT = "databricks-qwen3-embedding-0-6b"


def provision_index(client=None):
    # [CONTROL] Provision the serving endpoint and index idempotently.
    client = client or create_search_client()
    if not client.endpoint_exists(ENDPOINT_NAME):
        print(f"Creating AI Search endpoint: {ENDPOINT_NAME}")
        client.create_endpoint_and_wait(ENDPOINT_NAME, endpoint_type="STANDARD", verbose=True)

    try:
        client.get_index(ENDPOINT_NAME, INDEX_NAME)
        print(f"Index already exists: {INDEX_NAME}")
        return
    except NotFound:
        pass

    client.create_delta_sync_index(
        endpoint_name=ENDPOINT_NAME,
        source_table_name=SOURCE_TABLE,
        index_name=INDEX_NAME,
        pipeline_type="TRIGGERED",
        primary_key="chunk_id",
        embedding_source_column="chunk_text",
        embedding_model_endpoint_name=EMBEDDING_ENDPOINT,
        columns_to_sync=[
            "chunk_id",
            "file_id",
            "content_hash",
            "document_version",
            "is_active",
            "processed_at",
            "source_path",
            "source_file",
            "source_modified_at",
            "source_size_bytes",
            "vendor_id",
            "vendor_name",
            "support_tier",
            "region_covered",
            "chunk_index",
            "chunk_text",
            "token_count",
        ],
    )
    print(f"Index created and provisioning asynchronously: {INDEX_NAME}")


def main():
    provision_index()


if __name__ == "__main__":
    main()