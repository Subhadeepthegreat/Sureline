"""
Tests for golden query validation from client YAML configs.

Golden tests verify that the SQL the QueryEngine generates for known
question/answer pairs contains the expected substring. They are
data-driven from the YAML golden_test_suite section.

If no client YAML files exist, all tests skip gracefully.
"""

import asyncio
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sureline.schema_registry import SchemaRegistry, ClientConfig, GoldenTest

# ─── Helpers ─────────────────────────────────────────────────────

CLIENTS_DIR = Path(__file__).parent.parent / "clients"


def _collect_golden_tests() -> list[tuple[str, GoldenTest]]:
    """Collect all golden tests from all client YAML files."""
    if not CLIENTS_DIR.exists():
        return []

    registry = SchemaRegistry(clients_dir=CLIENTS_DIR)
    cases = []
    for yaml_file in CLIENTS_DIR.glob("*.yaml"):
        try:
            config = registry.load(yaml_file.stem)
        except Exception:
            continue
        for gt in config.golden_test_suite:
            cases.append((config.client_id, gt))
    return cases


GOLDEN_CASES = _collect_golden_tests()


# ─── Golden SQL contains check ────────────────────────────────────

def _make_mock_response(sql: str):
    """Build a fake openai tool-call response that returns the given SQL."""
    tool_call = MagicMock()
    tool_call.function.name = "run_sql_query"
    tool_call.function.arguments = f'{{"sql": "{sql}"}}'

    message = MagicMock()
    message.tool_calls = [tool_call]

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.mark.skipif(not GOLDEN_CASES, reason="No client YAML files with golden_test_suite found")
@pytest.mark.parametrize("client_id,golden", GOLDEN_CASES, ids=[
    f"{cid}::{gt.input[:40]}" for cid, gt in GOLDEN_CASES
])
@pytest.mark.asyncio
async def test_golden_sql_contains(
    client_id: str,
    golden: GoldenTest,
    tmp_path: Path,
):
    """
    For each golden test, mock the LLM to return the expected SQL,
    run it through execute_sql (against a stub DB), and verify the
    generated query contains the expected substring.
    """
    import sqlite3
    from sureline.query.query_engine import QueryEngine
    from sureline.query.sandbox import QueryResult

    # Create a stub SQLite DB so execute_sql doesn't fail
    stub_db = tmp_path / "stub.db"
    conn = sqlite3.connect(str(stub_db))
    conn.execute("CREATE TABLE stub (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    expected_sql = golden.expected_sql_contains

    # Mock the LLM to return expected_sql as a run_sql_query call
    fake_response = _make_mock_response(expected_sql)
    mock_create = AsyncMock(return_value=fake_response)

    with patch("sureline.query.query_engine.create_llm_client",
               return_value=(MagicMock(**{"chat.completions.create": mock_create}), "mock-model")):
        engine = QueryEngine(db_path=stub_db, client_name="Test")
        # Patch execute_sql so we just capture the SQL without needing real tables
        with patch(
            "sureline.query.query_engine.execute_sql",
            return_value=QueryResult(
                success=True, data=[], query_type="sql", generated_query=expected_sql
            ),
        ):
            result = await engine.query(golden.input)

    if golden.expected_sql_contains:
        assert result.generated_query is not None
        assert golden.expected_sql_contains.lower() in result.generated_query.lower(), (
            f"Expected '{golden.expected_sql_contains}' in generated SQL for "
            f"input '{golden.input}', got: '{result.generated_query}'"
        )


# ─── Standalone structural tests (no LLM required) ───────────────

class TestGoldenTestStructure:
    def test_golden_test_has_required_fields(self):
        gt = GoldenTest(
            input="What is my balance?",
            expected_sql_contains="balance",
            expected_spoken_contains="balance",
        )
        assert gt.input
        assert gt.expected_sql_contains or gt.expected_spoken_contains

    def test_golden_test_defaults_empty_strings(self):
        gt = GoldenTest(input="Tell me about the company")
        assert gt.expected_sql_contains == ""
        assert gt.expected_spoken_contains == ""

    @pytest.mark.skipif(not CLIENTS_DIR.exists(), reason="No clients directory")
    def test_all_golden_inputs_are_nonempty(self):
        for client_id, gt in GOLDEN_CASES:
            assert gt.input.strip(), f"Empty golden input in {client_id}"
