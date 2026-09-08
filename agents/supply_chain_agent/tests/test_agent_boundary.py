import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
AGENT_SOURCE = (ROOT / "src/agent_server/agent.py").read_text()
TOOLS_SOURCE = (ROOT / "src/agent_server/data_tools.py").read_text()


def test_agent_registers_only_contract_search():
    tree = ast.parse(AGENT_SOURCE)
    create_agent = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "create_agent")
    tools_assignment = next(
        node for node in ast.walk(create_agent)
        if isinstance(node, ast.keyword) and node.arg == "tools"
    )
    assert isinstance(tools_assignment.value, ast.List)
    assert len(tools_assignment.value.elts) == 1
    assert isinstance(tools_assignment.value.elts[0], ast.Name)
    assert tools_assignment.value.elts[0].id == "search_vendor_contracts"


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
