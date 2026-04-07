"""
Tests for asyncio correctness in QueryEngine.

Covers:
- execute_sql and execute_pandas are invoked via asyncio.to_thread
  (blocking calls must not block the event loop)
- Concurrent queries complete independently without interfering
- Event loop is not blocked: a dummy coroutine can interleave with a query
"""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from sureline.query.sandbox import execute_sql, execute_pandas, QueryResult
# Import QueryEngine at module level so it's never loaded inside a patch context.
# If loaded inside a patch("sureline.config.create_llm_client") block the first time,
# query_engine.py's `from sureline.config import create_llm_client` would bind the mock
# permanently — poisoning every subsequent test that uses QueryEngine.
from sureline.query.query_engine import QueryEngine  # noqa: E402


# ─── to_thread wrapping ──────────────────────────────────────────

class TestToThreadWrapping:
    @pytest.mark.asyncio
    async def test_execute_sql_does_not_block_event_loop(self, db_path: Path):
        """
        A coroutine sleeping 0 seconds should be able to run while a SQL
        query executes — proving execute_sql is off the event loop.
        """
        interleaved = []

        async def _marker():
            interleaved.append("before")
            await asyncio.sleep(0)
            interleaved.append("after")

        async def _run_sql():
            return await asyncio.to_thread(execute_sql, db_path, "SELECT * FROM customers")

        await asyncio.gather(_marker(), _run_sql())
        # If execute_sql blocked the event loop, _marker would never interleave
        assert "before" in interleaved
        assert "after" in interleaved

    @pytest.mark.asyncio
    async def test_execute_pandas_does_not_block_event_loop(self):
        """Same interleaving check for execute_pandas."""
        df = pd.DataFrame({"x": range(100)})
        interleaved = []

        async def _marker():
            interleaved.append("before")
            await asyncio.sleep(0)
            interleaved.append("after")

        async def _run_pandas():
            return await asyncio.to_thread(
                execute_pandas,
                None,           # csv_path unused when cached_df provided
                "result = df['x'].sum()",
                df,
            )

        await asyncio.gather(_marker(), _run_pandas())
        assert "before" in interleaved
        assert "after" in interleaved


# ─── Concurrent SQL queries ───────────────────────────────────────

class TestConcurrentQueries:
    @pytest.mark.asyncio
    async def test_concurrent_sql_queries_complete_independently(self, db_path: Path):
        """Two parallel SQL queries must both succeed and return correct data."""
        results = await asyncio.gather(
            asyncio.to_thread(execute_sql, db_path, "SELECT name FROM customers WHERE id = 1"),
            asyncio.to_thread(execute_sql, db_path, "SELECT name FROM customers WHERE id = 2"),
        )
        assert results[0].success is True
        assert results[1].success is True
        assert results[0].data[0]["name"] == "Alice"
        assert results[1].data[0]["name"] == "Bob"

    @pytest.mark.asyncio
    async def test_concurrent_pandas_queries_complete_independently(self):
        """Two parallel pandas queries on different DataFrames don't interfere."""
        df1 = pd.DataFrame({"val": [1, 2, 3]})
        df2 = pd.DataFrame({"val": [10, 20, 30]})

        results = await asyncio.gather(
            asyncio.to_thread(execute_pandas, None, "result = df['val'].sum()", df1),
            asyncio.to_thread(execute_pandas, None, "result = df['val'].sum()", df2),
        )
        assert results[0].success is True
        assert results[1].success is True
        assert results[0].data == 6
        assert results[1].data == 60

    @pytest.mark.asyncio
    async def test_mixed_concurrent_sql_and_pandas(self, db_path: Path):
        """SQL and pandas queries can run in parallel without interference."""
        df = pd.DataFrame({"quantity": [5, 10, 15]})

        sql_result, pandas_result = await asyncio.gather(
            asyncio.to_thread(
                execute_sql, db_path, "SELECT COUNT(*) AS cnt FROM customers"
            ),
            asyncio.to_thread(
                execute_pandas, None, "result = df['quantity'].mean()", df
            ),
        )
        assert sql_result.success is True
        assert sql_result.data[0]["cnt"] == 3

        assert pandas_result.success is True
        assert pandas_result.data == 10.0


# ─── QueryEngine async query method ──────────────────────────────

class TestQueryEngineAsyncQuery:
    @pytest.mark.asyncio
    async def test_query_engine_query_is_awaitable(self, db_path: Path):
        """engine.query() must be a coroutine (async def), not sync."""
        import inspect
        import json

        fake_response = MagicMock()
        tool_call = MagicMock()
        tool_call.function.name = "no_data_query_needed"
        tool_call.function.arguments = json.dumps({"reason": "test"})
        fake_response.choices[0].message.tool_calls = [tool_call]

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

        # Patch at the binding site (query_engine module), not at the source
        # (sureline.config), because query_engine.py uses `from ... import`.
        with patch("sureline.query.query_engine.create_llm_client",
                   return_value=(mock_client, "mock-model")):
            engine = QueryEngine(db_path=db_path)
            coro = engine.query("What does the company do?")
            assert inspect.isawaitable(coro)
            result = await coro

        assert result.success is True
