# RAG Agent Evaluation POC Plan

## Purpose

Build a small, reproducible POC that demonstrates evaluation of the custom contract RAG agent. The first milestone validates the evaluation loop, not production-scale retrieval performance.

## Cost And Scope

- Reuse the existing Databricks workspace, SQL warehouse, AI Search index, and model endpoints.
- Use the lowest-cost existing Databricks model endpoint that reliably returns structured judge output. Default to `databricks-llama-4-maverick` until a cheaper suitable endpoint is verified.
- Do not create a new warehouse, model deployment, index, storage account, or Azure resource for this POC.
- Use the three baseline contracts for the first evaluation cycle. Include VEND-321 only when its demo contract is active and record the corpus state.
- Expand the corpus only after the first evaluation cycle is green and the judge is calibrated.

## Separation Of Concerns

Production agent modules remain responsible only for retrieval, answer generation, and redacted observability. Evaluation code owns datasets, reference answers, judge prompts, scoring, reporting, and MLflow evaluation runs under `agents/supply_chain_agent/evaluation/`.

The evaluator will use adapters for the governed search function and deployed App response. It will not add judge calls, reference-answer logic, or evaluation-only branches to `agent.py` or `data_tools.py`.

## Preflight Gate

Run the read-only preflight before implementation or deployment changes. It must verify:

- Local Databricks profile and host configuration.
- Required Python packages and CLI tools.
- SQL warehouse visibility and usability.
- Catalog/schema visibility and source/index/function access.
- UC trace table visibility.
- Candidate judge endpoint visibility and query permission where observable.
- Deployed App status without starting or modifying it.

Missing credentials or permissions are reported as blockers. The preflight must not create, start, stop, or alter resources.

## Evaluation Dataset

Create a versioned JSONL dataset of 10-20 cases covering:

- Single-vendor facts.
- Multi-chunk questions.
- Cross-vendor comparisons.
- Vendor, tier, and region filters.
- Empty-result and negative cases.
- Hallucination traps and unsupported-specificity questions.

Each case stores the question, optional filters, reference answer, normalized required facts, expected source/chunk identifiers, exact chunk text captured from the live source/index, acceptable citations, and expected empty/error behavior.

The authoritative chunking is `cl100k_base`, 500-token windows, and a 450-token step. Chunk annotations must be generated or validated from the live table/index rather than guessed from source files.

## Metrics

### Retrieval

- Precision@K and recall@K against expected chunks/facts.
- F1 and first relevant rank.
- Required-fact coverage.
- Irrelevant-context rate.

### Generation

Use a Databricks-hosted LLM judge with a versioned prompt and strict JSON output to score:

- Context precision.
- Context recall.
- Groundedness/entailment.
- Completeness.
- Contradictions and unsupported claims.
- Citation correctness.

Cache judge results by dataset case, prompt version, model endpoint, and input hash. Log judge rationale and confidence separately from aggregate metrics.

## Implementation Sequence

1. Run `scripts/local/evaluation_preflight.sh` and resolve blockers.
2. Add dataset schema and a corpus snapshot/annotation validator under `evaluation/`.
3. Extend the existing runner with deterministic retrieval metrics while preserving current mock tests.
4. Add a final-answer invocation adapter and judge adapter under `evaluation/`.
5. Add calibration fixtures and evaluator tests.
6. Log per-case and aggregate evaluation metrics to the UC-v2 experiment, separate from production traces.
7. Add a repeatable Databricks evaluation job only after the local path is green.
8. Document commands, permissions, and acceptance criteria.

## Acceptance Criteria

- Existing agent boundary and evaluation tests pass.
- Dataset records validate against the active corpus and index.
- Retrieval metrics are produced per case and in aggregate.
- Final answers are judged against retrieved evidence.
- Evaluation metrics appear in `/Shared/globalmart-supply-chain-agent-uc-v2-dev`.
- UC traces remain in `globalmart.agent_observability`.
- No raw question, answer, or contract evidence is added to production span attributes.
- No new cost-bearing Azure or Databricks resources are required.

## Required Inputs

The implementation can proceed locally with defaults, but live evaluation requires:

- A usable Databricks CLI/SDK profile, assumed initially to be `dbai-dev`.
- Workspace host and catalog, assumed `globalmart`.
- SQL warehouse ID, assumed `a749a7ee30b8f4f4`.
- A running or invokable `dbai-supply-agent-dev` App.
- Permission to query the governed search function and UC trace tables.
- An existing model endpoint for judging; use the cheapest suitable endpoint available.
