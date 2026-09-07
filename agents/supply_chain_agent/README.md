# Supply Chain Contract Agent

This agent is the vector-search specialist for vendor contracts. Its runtime exposes only `search_vendor_contracts`, which queries the governed SQL function backed by the managed AI Search index.

It does not expose inventory lookup, model-generated SQL, Bronze/Silver tables, or a Delta-source fallback. Inventory analytics and ingestion remain separate platform capabilities and can be owned by future agents.

The agent-specific Bundle is [databricks.yml](databricks.yml). Its runnable package is under `src/agent_server`, with its own `pyproject.toml`, App configuration, UI, and tests boundary.
