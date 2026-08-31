# GlobalMart Technical Architecture

## 1. Scope

This document describes the deployed Azure Databricks Premium architecture for the GlobalMart supply-chain RAG and Text-to-SQL demonstration.

The implementation uses one contract dataset: `vendor_contract_chunks_index_source`, a regular Delta table written by a serverless batch refresh job and consumed by the managed AI Search index. The earlier Lakeflow pipeline and duplicate outputs were removed because this workspace does not accept Streaming Tables or Materialized Views as AI Search sources.

The selected conversational architecture is a Custom Databricks App. It provides the user interface and explicitly orchestrates structured SQL, AI Search retrieval, and grounded answer generation.

## 2. Architecture Options

| Option | Demo speed | Initial cost | Hybrid SQL + RAG control | User experience | Best fit |
|---|---:|---:|---|---|---|
| Genie Agent with semantic layer and search UDF | Fastest if supported | Lowest | Medium; depends on UDF and Genie behavior | Built-in BI chat | SQL-first BI questions with limited retrieval |
| Agent Framework endpoint with SQL and AI Search tools | Fast | Low/medium | High | AI Playground or API initially | Reliable conversational orchestration |
| Custom Databricks App with Agent Framework | Medium | Medium | Highest | Dedicated branded chat and citations | This project and production-style demos |

Genie is the least expensive proof of concept when its semantic layer can call the required search function. A search UDF can be a platform dependency and may be awkward for returning ranked chunks, applying filters, and exposing citations. Agent Framework makes tool selection, SQL safety, retrieval, grounding, and observability explicit.

## 3. Selected Architecture: Custom Databricks App

The app is a thin conversational product layer over Databricks services:

1. The user submits a natural-language supply-chain question to the App.
2. The App's AgentServer classifies the request as structured, contract-oriented, or hybrid.
3. The SQL tool generates and validates read-only SQL, then executes it against the SQL Warehouse.
4. The AI Search tool queries `vendor_contract_chunks_index_rebuilt` with vendor, tier, or region filters when available.
5. The agent combines returned facts and contract evidence into one answer with source-file and chunk citations.
6. The app renders the answer, citations, and a concise evidence trace.

The App is both the interface and AgentServer hosting boundary; Agent Framework
is the reasoning and orchestration layer. The SQL Warehouse, Delta tables,
Volume, AI Search endpoint, embedding endpoint, and managed index remain
platform services rather than app-owned data stores.

The app should enforce read-only SQL, allow-list the three demo tables, cap result sizes, pass user identity to Databricks tools, and return a transparent fallback when SQL or retrieval is unavailable.

Deployment compatibility is checked by
`scripts/local/validate_demo_workspace.py` before AI Search provisioning. It verifies
that the source is a regular Delta table with Change Data Feed and the required
schema; it rejects Streaming Tables and Materialized Views. Unity Catalog row
filters, column masks, and privileges remain workspace governance controls and
must not be inferred from the agent prompt. AI Search authorization requires a
separate design because the managed index is a serving copy of the source.

## 4. System Context, C4 Level 1

```mermaid
C4Context
    title GlobalMart Supply Chain Intelligence Demo - System Context

    Person(operations, "Operations Manager", "Investigates delayed inventory and vendor performance")
    Person(procurement, "Procurement Manager", "Reviews contract terms, penalties, and vendor obligations")
    System(demo, "Custom Databricks Supply Chain App", "Conversational SQL and contract retrieval")
    System_Ext(databricks, "Azure Databricks Premium", "Runs Delta tables, serverless jobs, model serving, and SQL")
    System_Ext(aisearch, "Databricks AI Search", "Indexes and retrieves contract chunks")
    System_Ext(volume, "Unity Catalog Volume", "Stores vendor contract files")
    System_Ext(model, "Databricks Model Serving", "Agent and answer generation")

    Rel(operations, demo, "Asks inventory and delay questions")
    Rel(procurement, demo, "Asks contract and liability questions")
    Rel(demo, databricks, "Runs SQL and orchestration")
    Rel(demo, aisearch, "Performs semantic retrieval")
    Rel(demo, model, "Orchestrates tools and generates answers")
    Rel(databricks, volume, "Reads source contracts")
    Rel(aisearch, databricks, "Syncs from Delta source")
```

## 5. Container Diagram, C4 Level 2

```mermaid
C4Container
    title GlobalMart Supply Chain Intelligence Demo - Containers

    Person(user, "Business User", "Operations or procurement stakeholder")

    System_Boundary(platform, "Azure Databricks Premium") {
        Container(app, "Custom Databricks App", "App UI and server", "Accepts questions and renders grounded answers")
        Container(agent, "Agent Framework", "Agent endpoint", "Routes questions to SQL and AI Search tools")
        Container(bundle, "Declarative Automation Bundle", "databricks.yml and resources/*.yml", "Deploys serverless jobs")
        Container(datajob, "Mock Data Job", "Serverless notebook", "Writes dim_products, dim_vendors, and fact_inventory_status")
        Container(refreshjob, "Contract Refresh Job", "Serverless notebook", "Reads files, chunks text, and writes a regular Delta table")
        Container(delta, "Delta Data Layer", "Unity Catalog tables", "Stores structured facts, dimensions, and searchable chunks")
        Container(sql, "Databricks SQL Editor", "SQL", "Runs auditable Text-to-SQL queries")
    }

    System_Ext(volume, "Unity Catalog Volume", "Contract files")
    System_Ext(index, "Managed AI Search Index", "Triggered Delta Sync index")
    System_Ext(model, "Embedding Model Endpoint", "databricks-qwen3-embedding-0-6b")

    Rel(user, app, "Asks natural-language questions")
    Rel(app, agent, "Sends question and conversation")
    Rel(agent, sql, "Uses read-only SQL tool")
    Rel(agent, index, "Uses retrieval tool")
    Rel(bundle, datajob, "Deploys and runs")
    Rel(bundle, refreshjob, "Deploys and runs")
    Rel(datajob, delta, "Writes structured tables")
    Rel(volume, refreshjob, "Reads contract files")
    Rel(refreshjob, delta, "Incrementally updates [GOLD] index source")
    Rel(delta, sql, "Provides SQL tables")
    Rel(delta, index, "Delta Sync source")
    Rel(index, model, "Creates embeddings")
```

## 4. Component Diagram, C4 Level 3

```mermaid
C4Component
    title Contract Refresh Job - Components

    Container_Boundary(refresh, "refresh_vendor_contract_chunks.py") {
        Component(reader, "Binary File Reader", "Spark binaryFile", "Loads supported files from the Volume")
        Component(extractor, "Text Extractor", "pypdf and UTF-8 decoder", "Extracts text from PDF and text-like files")
        Component(tokenizer, "Tokenization and Windowing", "tiktoken cl100k_base", "Creates 500-token windows with a 450-token step")
        Component(metadata, "Vendor Metadata Mapper", "Python mapping and regex", "Derives vendor ID and business metadata from filename")
        Component(identity, "Chunk ID Generator", "SHA-256", "Creates deterministic chunk identifiers")
        Component(writer, "Delta Writer", "Spark DataFrame and Delta MERGE", "Incrementally updates the [GOLD] regular Delta source table")
    }

    Container_Ext(volume, "Unity Catalog Volume")
    Container_Ext(delta, "vendor_contract_chunks_index_source", "Regular Delta table")

    Rel(volume, reader, "Reads binary content")
    Rel(reader, extractor, "Passes path and bytes")
    Rel(extractor, tokenizer, "Passes normalized text")
    Rel(tokenizer, metadata, "Produces chunk text")
    Rel(metadata, identity, "Adds vendor fields")
    Rel(identity, writer, "Produces complete rows")
    Rel(writer, delta, "Saves rows")
```

## 7. Data Flow

```mermaid
flowchart LR
    A[Contract files in Unity Catalog Volume] --> B[Batch binaryFile read]
    B --> C{File type}
    C -->|PDF| D[pypdf text extraction]
    C -->|TXT MD CSV JSON HTML| E[UTF-8 decode]
    D --> F[Normalize whitespace]
    E --> F
    F --> G[tiktoken cl100k_base]
    G --> H[500-token windows]
    H --> I[450-token step]
    I --> J[50-token overlap]
    J --> K[Vendor metadata mapping]
    K --> L[Deterministic SHA-256 chunk_id]
    L --> M[Regular Delta source table]
    M --> N[Triggered Delta Sync]
    N --> O[Managed AI Search index]
```

## 8. Structured Analytics Flow

```mermaid
flowchart LR
    A[generate_mock_data job] --> B[dim_products]
    A --> C[dim_vendors]
    A --> D[fact_inventory_status]
    B --> E[Databricks SQL]
    C --> E
    D --> E
    E --> F[Delayed inventory value]
    E --> G[Vendor and account-manager analysis]
```

## 9. Key Contracts and Names

| Resource | Value |
|---|---|
| Workspace | `https://adb-7405616725207770.10.azuredatabricks.net` |
| Bundle | `dbai` |
| Target | `dev` |
| Catalog/schema | `globalmart.supply_chain` |
| Contract Volume | `/Volumes/globalmart/supply_chain/vendor_contracts` |
| Structured tables | `dim_products`, `dim_vendors`, `fact_inventory_status` |
| AI Search source | `vendor_contract_chunks_index_source` |
| Managed index | `vendor_contract_chunks_index_rebuilt` |
| AI Search endpoint | `globalmart-supply-chain-search` |
| Embedding endpoint | `databricks-qwen3-embedding-0-6b` |
| Index mode | `TRIGGERED` |
| Window size | 500 tokens |
| Window step | 450 tokens |
| Overlap | 50 tokens |

## 10. Operational Design

The structured data job is an explicit serverless notebook run. The contract source refresh is also an explicit serverless notebook run because it performs a batch overwrite of the regular Delta source. The AI Search index is triggered separately after the source table refresh completes.

This separation makes freshness visible:

1. Source files are uploaded or replaced.
2. Contract refresh job completes.
3. AI Search sync is triggered.
4. Index status becomes Online and update status becomes Completed.
