import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
AGENT_SOURCE = (ROOT / "src/agent_server/agent.py").read_text()
TOOLS_SOURCE = (ROOT / "src/agent_server/data_tools.py").read_text()


def test_agent_has_no_text_to_sql_or_lifecycle_sources():
    for source in (AGENT_SOURCE, TOOLS_SOURCE):
        assert "query_inventory" not in source
        assert "lookup_inventory" not in source
        assert "contract_file_events_bronze" not in source
        assert "contract_file_manifest" not in source
        assert "contract_documents_silver" not in source
        assert "vendor_contract_chunks_index_source" not in source
        assert "_vendor_contract_fallback" not in source


def test_agent_returns_structured_contract_evidence_for_citations():
    assert 'custom_outputs={"contract_evidence": evidence}' in AGENT_SOURCE
    assert "source_file" in TOOLS_SOURCE
    assert "chunk_text" in TOOLS_SOURCE


def test_agent_exposes_streaming_and_session_contracts():
    assert "@stream()" in AGENT_SOURCE
    assert "normalize_history_items" in AGENT_SOURCE
    assert "mlflow.trace.session" in AGENT_SOURCE
