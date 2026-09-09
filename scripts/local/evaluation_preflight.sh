#!/usr/bin/env bash
set -uo pipefail

# Read-only prerequisite checks for the low-cost RAG evaluation POC.
PROFILE="${DATABRICKS_CONFIG_PROFILE:-dbai-dev}"
CATALOG="${DBAI_CATALOG:-globalmart}"
SCHEMA="${DBAI_SCHEMA:-supply_chain}"
TRACE_SCHEMA="${MLFLOW_TRACE_SCHEMA:-agent_observability}"
WAREHOUSE_ID="${DATABRICKS_SQL_WAREHOUSE_ID:-a749a7ee30b8f4f4}"
APP_NAME="${DBAI_APP_NAME:-dbai-supply-agent-dev}"
JUDGE_ENDPOINT="${EVALUATION_JUDGE_ENDPOINT:-${MODEL_ENDPOINT:-databricks-llama-4-maverick}}"

passed=0
blocked=0
skipped=0

pass_check() {
  printf 'PASS  %s\n' "$1"
  passed=$((passed + 1))
}

block_check() {
  printf 'BLOCK %s\n' "$1"
  blocked=$((blocked + 1))
}

skip_check() {
  printf 'SKIP  %s\n' "$1"
  skipped=$((skipped + 1))
}

printf 'Evaluation POC preflight (read-only)\n'
printf 'profile=%s catalog=%s schema=%s warehouse=%s app=%s judge=%s\n\n' \
  "$PROFILE" "$CATALOG" "$SCHEMA" "$WAREHOUSE_ID" "$APP_NAME" "$JUDGE_ENDPOINT"

if command -v databricks >/dev/null 2>&1; then
  pass_check "databricks CLI is installed"
else
  block_check "databricks CLI is not installed"
fi

if command -v python3 >/dev/null 2>&1; then
  pass_check "python3 is installed"
else
  block_check "python3 is not installed"
fi

if command -v jq >/dev/null 2>&1; then
  pass_check "jq is installed"
else
  block_check "jq is not installed"
fi

if command -v databricks >/dev/null 2>&1; then
  auth_output="$(databricks auth describe --profile "$PROFILE" --output json 2>&1)"
  auth_status=$?
  if [[ "$auth_status" -eq 0 ]]; then
    host="$(printf '%s' "$auth_output" | jq -r '.details.host // empty' 2>/dev/null || true)"
    if [[ -n "$host" ]]; then
      pass_check "Databricks profile is authenticated (host: $host)"
    else
      pass_check "Databricks profile is authenticated"
    fi
  else
    block_check "Databricks profile '$PROFILE' is not authenticated"
  fi
else
  skip_check "Databricks authentication (CLI unavailable)"
fi

run_cli_check() {
  local label="$1"
  shift
  local output status
  output="$(databricks "$@" --profile "$PROFILE" --output json 2>&1)"
  status=$?
  if [[ "$status" -eq 0 ]]; then
    pass_check "$label"
  else
    block_check "$label"
    printf '      %s\n' "$(printf '%s' "$output" | tail -n 1)"
  fi
}

if command -v databricks >/dev/null 2>&1; then
  run_cli_check "SQL warehouse is readable" warehouses get "$WAREHOUSE_ID"
  run_cli_check "source table is readable" tables get "${CATALOG}.${SCHEMA}.vendor_contract_chunks_index_source"
  run_cli_check "managed search index table is readable" tables get "${CATALOG}.${SCHEMA}.vendor_contract_chunks_index_rebuilt"
  run_cli_check "governed search function is readable" functions get "${CATALOG}.${SCHEMA}.search_vendor_contracts"
  run_cli_check "UC trace span table is readable" tables get "${CATALOG}.${TRACE_SCHEMA}.contract_agent_traces_otel_spans"
  run_cli_check "judge endpoint is readable" serving-endpoints get "$JUDGE_ENDPOINT"
  run_cli_check "deployed App status is readable (no start/stop)" apps get "$APP_NAME"
else
  skip_check "Databricks resource checks (CLI unavailable)"
fi

printf '\nSummary: passed=%d blocked=%d skipped=%d\n' "$passed" "$blocked" "$skipped"
if [[ "$blocked" -gt 0 ]]; then
  printf 'Preflight is blocked. Resolve the reported permissions or configuration before live evaluation.\n'
  exit 1
fi
printf 'Preflight passed. No resources were created, started, stopped, or modified.\n'
