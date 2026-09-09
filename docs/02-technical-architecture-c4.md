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
| Custom Agent | Agent Framework orchestration in `agents/supply_chain_agent/src/agent_server/agent.py`, with governed contract retrieval in `data_tools.py` | User asks contract questions through the App, or the agent is invoked through its hosting boundary | Vector retrieval grounding and citations |
| Databricks App | Databricks App serving the MLflow AgentServer and UI in `agents/supply_chain_agent/static/index.html` | User interacts with a dedicated contract-search application | Branded interaction, conversation state, evidence presentation, and application controls |

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
2. `agent.py` routes the question to the contract retrieval tool.
3. `data_tools.py` calls the governed SQL function backed by the managed AI
    Search index with optional vendor, tier, and region filters.
4. The model summarizes retrieved evidence and returns citations to the App UI.

The Genie path uses the same tables and retrieval function through the Genie
semantic layer. The App path adds explicit orchestration and user-visible
evidence handling; neither path bypasses Unity Catalog or endpoint permissions.

## 4. Experience Overview

The following view shows how the three experiences are implemented around the
shared platform resources:

```mermaid
flowchart LR
    User[Operations or procurement user]
    Genie["Genie Agent<br/>Managed Genie space"]
    App["Databricks App<br/>UI and MLflow AgentServer"]
    Custom["Custom Agent<br/>Agent Framework orchestration"]
    SQL[SQL Warehouse]
    Gold[(Unity Catalog Gold tables)]
    Volume[(Contract Volume)]
    Refresh[Contract refresh job]
    Source[(Regular Delta source table)]
    Search[AI Search index]
    Function["search_vendor_contracts<br/>SQL table-valued function"]
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
flowchart LR
    Operations[Operations Manager]
    Procurement[Procurement Manager]
    Genie[Databricks Genie Agent]
    App[Custom Databricks Supply Chain App]
    Databricks[Azure Databricks Premium]
    AISearch[Databricks AI Search]
    Volume[Unity Catalog Volume]
    Model[Databricks Model Serving]

    Operations -->|SQL-first inventory questions| Genie
    Procurement -->|Governed vendor questions| Genie
    Operations -->|Structured or mixed questions| App
    Procurement -->|Grounded contract questions| App
    Genie -->|Semantic layer and SQL| Databricks
    App -->|Custom-agent tools| Databricks
    App -->|Semantic retrieval| AISearch
    App -->|Grounded answer generation| Model
    Databricks -->|Reads source contracts| Volume
    AISearch -->|Syncs from Delta source| Databricks
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
flowchart LR
    User[Business User]
    App[Custom Databricks App]
    Agent[Agent Framework]
    Bundle[Declarative Automation Bundle]
    DataJob[Mock Data Job]
    RefreshJob[Contract Refresh Job]
    Delta[Unity Catalog Delta Data Layer]
    SQL[Databricks SQL Editor]
    Volume[Unity Catalog Volume]
    Index[Managed AI Search Index]
    Model[Embedding Model Endpoint]

    User -->|Natural-language questions| App
    App -->|Question and conversation| Agent
    Agent -->|Read-only SQL tool| SQL
    Agent -->|Retrieval tool| Index
    Bundle -->|Deploys and runs| DataJob
    Bundle -->|Deploys and runs| RefreshJob
    DataJob -->|Writes structured tables| Delta
    Volume -->|Contract files| RefreshJob
    RefreshJob -->|Incremental Gold index source| Delta
    Delta -->|SQL tables| SQL
    Delta -->|Delta Sync source| Index
    Index -->|Creates embeddings| Model
```

## 7. Component Diagram, C4 Level 3

```mermaid
flowchart LR
    Volume[Unity Catalog Volume]
    Reader[Binary File Reader<br/>Spark binaryFile]
    Extractor[Text Extractor<br/>pypdf and UTF-8 decoder]
    Tokenizer[Tokenization and Windowing<br/>tiktoken cl100k_base]
    Metadata[Vendor Metadata Mapper<br/>Python mapping and regex]
    Identity[Chunk ID Generator<br/>SHA-256]
    Writer[Delta Writer<br/>Spark DataFrame and Delta MERGE]
    Delta[vendor_contract_chunks_index_source<br/>Regular Delta table]

    Volume -->|Binary content| Reader
    Reader -->|Path and bytes| Extractor
    Extractor -->|Normalized text| Tokenizer
    Tokenizer -->|Chunk text| Metadata
    Metadata -->|Vendor fields| Identity
    Identity -->|Deterministic rows| Writer
    Writer -->|Saves rows| Delta
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
| Workspace | `https://adb-7405617519191024.4.azuredatabricks.net` |
| Bundle | `dbai` |
| Target | `dev` |
| Catalog/schema | `globalmart.supply_chain` |
| Contract Volume | `/Volumes/globalmart/supply_chain/vendor_contracts` |
| Structured tables | `dim_products`, `dim_vendors`, `fact_inventory_status` |
| AI Search source | `vendor_contract_chunks_index_source` |
| Managed index | `vendor_contract_chunks_index_rebuilt` |
| AI Search endpoint | `globalmart-supply-chain-search` |
| Embedding endpoint | `databricks-qwen3-embedding-0-6b` |
| App | `dbai-supply-agent-dev` |
| MLflow experiment | `/Shared/globalmart-supply-chain-agent-uc-v2-dev` |
| MLflow trace location | `globalmart.agent_observability.contract_agent_traces` |
| MLflow trace warehouse | SQL Warehouse `a749a7ee30b8f4f4` |
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
