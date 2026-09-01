# GlobalMart Technical Architecture

## 1. Scope

This document describes the deployed Azure Databricks Premium architecture for the GlobalMart supply-chain RAG and Text-to-SQL demonstration.

The implementation uses one contract dataset: `vendor_contract_chunks_index_source`, a regular Delta table written by a serverless batch refresh job and consumed by the managed AI Search index. The earlier Lakeflow pipeline and duplicate outputs were removed because this workspace does not accept Streaming Tables or Materialized Views as AI Search sources.

The deployed design supports three complementary conversational experiences:
Databricks Genie for SQL-first exploration, a custom Agent Framework agent for
explicit SQL and retrieval orchestration, and a Databricks App that hosts that
custom agent behind a dedicated user interface. Genie and the custom agent use
the same governed data foundation but have different interaction and control
boundaries.

## 2. Architecture Options

| Option | Demo speed | Initial cost | Hybrid SQL + RAG control | User experience | Best fit |
|---|---:|---:|---|---|---|
| Genie Agent with semantic layer and search UDF | Fastest if supported | Lowest | Medium; depends on UDF and Genie behavior | Built-in BI chat | SQL-first BI questions with limited retrieval |
| Agent Framework endpoint with SQL and AI Search tools | Fast | Low/medium | High | AI Playground or API initially | Reliable conversational orchestration |
| Custom Databricks App with Agent Framework | Medium | Medium | Highest | Dedicated branded chat and citations | This project and production-style demos |

Genie is the least expensive proof of concept when its semantic layer can call the required search function. A search UDF can be a platform dependency and may be awkward for returning ranked chunks, applying filters, and exposing citations. Agent Framework makes tool selection, SQL safety, retrieval, grounding, and observability explicit.

## 3. Three Conversational Experiences

The system does not select one conversational architecture. It exposes the
same business domain through three complementary paths:

| Experience | Implementation | Primary interaction | Strength |
|---|---|---|---|
| Genie Agent | Databricks Genie space over Unity Catalog tables and `search_vendor_contracts` | User asks questions in the Genie UI | Fast SQL-first analysis with a managed conversational experience |
| Custom Agent | Agent Framework orchestration in `app/agent_server/agent.py`, with governed tools in `data_tools.py` and SQL validation in `sql_guard.py` | User asks questions through the App, or the agent is invoked through its hosting boundary | Explicit routing, read-only SQL, retrieval grounding, and citations |
| Databricks App | Databricks App serving the MLflow AgentServer and the UI in `app/static/index.html` | User interacts with a dedicated chat application | Branded interaction, conversation state, evidence presentation, and application controls |

The custom agent and Databricks App are one deployed product boundary: the App
hosts the custom agent. They are listed separately because the agent is the
reasoning implementation while the App is the user-facing runtime and delivery
surface. There is no second independent custom-agent data store.

### Shared data and control plane

All three experiences use the same governed resources:

- Unity Catalog Gold tables provide structured inventory and vendor facts.
- The contract Volume is the source for the batch refresh job.
- `vendor_contract_chunks_index_source` is the regular Delta source for AI Search.
- `vendor_contract_chunks_index_rebuilt` serves active contract chunks.
- `search_vendor_contracts` exposes bounded retrieval to Genie and SQL clients.
- SQL Warehouse, Unity Catalog permissions, row filters, and column masks remain
    enforcement points rather than prompt instructions.
- The model-serving endpoint generates answers for the custom agent; Genie uses
    its managed semantic and model experience.

The custom agent follows this path:

1. The user submits a natural-language question through the Databricks App.
2. `agent.py` routes the question to structured SQL, contract retrieval, or both.
3. `data_tools.py` executes allow-listed read-only SQL or queries the managed
     AI Search index with optional vendor, tier, and region filters.
4. `sql_guard.py` rejects disallowed SQL before the SQL Warehouse is called.
5. The model combines governed facts and retrieved evidence, then returns
     citations and a grounded answer to the App UI.

The Genie path uses the same tables and retrieval function through the Genie
semantic layer. The App path adds explicit orchestration and user-visible
evidence handling; neither path bypasses Unity Catalog or endpoint permissions.

## 4. Experience Overview

The following view shows how the three experiences are implemented around the
shared platform resources:

```mermaid
flowchart LR
    User[Operations or procurement user]
    Genie[Genie Agent\nManaged Genie space]
    App[Databricks App\nUI and MLflow AgentServer]
    Custom[Custom Agent\nAgent Framework orchestration]
    SQL[SQL Warehouse]
    Gold[(Unity Catalog Gold tables)]
    Volume[(Contract Volume)]
    Refresh[Contract refresh job]
    Source[(Regular Delta source table)]
    Search[AI Search index]
    Function[search_vendor_contracts\nSQL table-valued function]
    Model[Model Serving endpoint]

    User -->|SQL-first questions| Genie
    User -->|Structured, contract, or hybrid questions| App
    App --> Custom
    Genie -->|Semantic layer and SQL| SQL
    Genie -->|Contract retrieval function| Function
    Custom -->|Read-only SQL tool| SQL
    Custom -->|Retrieval tool| Search
    Custom -->|Answer generation| Model
    SQL --> Gold
    Function --> Search
    Volume --> Refresh --> Source --> Search
    Search -->|Indexed contract evidence| Function
```

The user can choose Genie for managed SQL-first exploration or the App for
custom orchestration and a dedicated interface. The custom agent is not a
separate user interface: it is hosted by the App and calls the same SQL,
function, and search resources.

The two request paths have different control points:

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Genie as Genie Agent
    participant App as Databricks App
    participant Agent as Custom Agent
    participant SQL as SQL Warehouse
    participant Function as search_vendor_contracts
    participant Search as AI Search index
    participant Model as Model Serving

    alt Managed Genie path
        User->>Genie: Ask SQL or contract question
        Genie->>SQL: Generate governed SQL
        SQL-->>Genie: Structured facts
        opt Contract question
            Genie->>Function: Call retrieval function
            Function->>Search: Hybrid search with filters
            Search-->>Function: Active contract chunks
            Function-->>Genie: Bounded evidence and scores
        end
        Genie-->>User: Conversational answer
    else Custom App path
        User->>App: Submit structured, contract, or hybrid question
        App->>Agent: Forward question and conversation
        Agent->>Agent: Route to one or both tools
        Agent->>SQL: Execute allow-listed read-only query
        SQL-->>Agent: Structured facts
        opt Contract question
            Agent->>Search: Retrieve active chunks with filters
            Search-->>Agent: Contract evidence
        end
        Agent->>Model: Generate grounded response
        Model-->>Agent: Answer and citations
        Agent-->>App: Renderable response
        App-->>User: Answer, evidence, and citations
    end
```

## 5. System Context, C4 Level 1

```mermaid
C4Context
    title GlobalMart Supply Chain Intelligence Demo - System Context

    Person(operations, "Operations Manager", "Investigates delayed inventory and vendor performance")
    Person(procurement, "Procurement Manager", "Reviews contract terms, penalties, and vendor obligations")
    System(genie, "Databricks Genie Agent", "Managed SQL-first conversational analysis")
    System(app, "Custom Databricks Supply Chain App", "Dedicated UI hosting the custom Agent Framework agent")
    System_Ext(databricks, "Azure Databricks Premium", "Runs Delta tables, serverless jobs, model serving, and SQL")
    System_Ext(aisearch, "Databricks AI Search", "Indexes and retrieves contract chunks")
    System_Ext(volume, "Unity Catalog Volume", "Stores vendor contract files")
    System_Ext(model, "Databricks Model Serving", "Agent and answer generation")

    Rel(operations, genie, "Asks SQL-first inventory questions")
    Rel(procurement, genie, "Asks governed vendor questions")
    Rel(operations, app, "Asks structured or mixed questions")
    Rel(procurement, app, "Asks grounded contract questions")
    Rel(genie, databricks, "Uses semantic layer and SQL")
    Rel(app, databricks, "Uses custom-agent tools")
    Rel(app, aisearch, "Performs semantic retrieval")
    Rel(app, model, "Generates grounded answers")
    Rel(databricks, volume, "Reads source contracts")
    Rel(aisearch, databricks, "Syncs from Delta source")
```

Deployment compatibility is checked by
`scripts/local/validate_demo_workspace.py` before AI Search provisioning. It verifies
that the source is a regular Delta table with Change Data Feed and the required
schema; it rejects Streaming Tables and Materialized Views. Unity Catalog row
filters, column masks, and privileges remain workspace governance controls and
must not be inferred from the agent prompt. AI Search authorization requires a
separate design because the managed index is a serving copy of the source.

## 6. Container Diagram, C4 Level 2

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

## 7. Component Diagram, C4 Level 3

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

## 8. Data Flow

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

## 9. Structured Analytics Flow

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

## 10. Key Contracts and Names

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

## 11. Operational Design

The structured data job is an explicit serverless notebook run. The contract source refresh is also an explicit serverless notebook run because it performs a batch overwrite of the regular Delta source. The AI Search index is triggered separately after the source table refresh completes.

This separation makes freshness visible:

1. Source files are uploaded or replaced.
2. Contract refresh job completes.
3. AI Search sync is triggered.
4. Index status becomes Online and update status becomes Completed.
