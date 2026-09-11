# agent-supply-chain-contract-ka

This agent is the vector-search specialist for vendor contracts. Its runtime exposes only `search_vendor_contracts`, which queries the governed SQL function backed by the managed AI Search index.

It does not expose inventory lookup, model-generated SQL, Bronze/Silver tables, or a Delta-source fallback. Inventory analytics and ingestion remain separate platform capabilities and can be owned by future agents.

The agent-specific Bundle is [databricks.yml](databricks.yml). Its runnable package is under `src/agent_server`, with its own `pyproject.toml`, App configuration, UI, and tests boundary.

The ResponsesAgent response includes `custom_outputs.contract_evidence`. The custom UI turns citations into expandable evidence panels so users can inspect the exact active-contract chunk behind an answer.

MLflow traces use the workspace-absolute experiment `/Shared/globalmart-supply-chain-agent-uc-v2-dev` and a Unity Catalog trace location at `globalmart.agent_observability`, with table prefix `contract_agent_traces`. The runtime records redacted invocation and retrieval spans; contract evidence and SQL text are not stored as span attributes. Run the deterministic evaluation suite with `PYTHONPATH=src uv run --with pytest python -m pytest -q tests` and run the opt-in live evaluator with `PYTHONPATH=src uv run python -m evaluation.runner --mode live`.

After invoking the deployed App at least once, verify the non-streaming trace contract with `MLFLOW_TRACING_SQL_WAREHOUSE_ID="$DATABRICKS_SQL_WAREHOUSE_ID" uv run python -m evaluation.verify_traces --experiment "${MLFLOW_EXPERIMENT_NAME:-/Shared/globalmart-supply-chain-agent-uc-v2-dev}" --trace-location globalmart.agent_observability.contract_agent_traces`. After exercising the streamed UI path, add `--require-stream` to verify the stream span as well.

This App uses deterministic retrieval followed by Chat Completions because `databricks-llama-4-maverick` does not support Responses API passthrough and does not reliably execute the tool loop. The supported MLflow `ResponsesAgent` transport is retained. The custom UI also intentionally keeps `enable_chat_proxy=False`; it is the product UI rather than the template chat proxy.

The agent supports multi-turn requests: the UI sends conversation history, the runtime normalizes replayed assistant messages, preserves a session ID in MLflow traces, and uses recent user turns to resolve vendor follow-ups. Conversation simulation is intentionally disabled. The two enabled MLflow LLM judges are `Correctness` and `RelevanceToQuery`, both configured to use `databricks:/databricks-meta-llama-3-1-8b-instruct`; production monitoring and additional judges are disabled.

Unity Gateway routing is supported but safe-off by default until the Gateway model service exists. Create or authorize a Unity Gateway model service named `agent-supply-chain-contract-ka-model`, then set `USE_AI_GATEWAY=true` and change `AI_GATEWAY_ENDPOINT` in the Databricks App configuration. These environment values can also be changed through the Bundle variables in `resources/agent.resources.yml`; no Python change is required to switch models or Gateway destinations.

The SQL warehouse is an App resource binding exposed through `valueFrom: sql-warehouse`. The local deployment script uses Bundle sync plus App deployment because the current CLI rejects the existing App's Bundle update mask; the resource binding remains declared in the Bundle for reconciliation.
