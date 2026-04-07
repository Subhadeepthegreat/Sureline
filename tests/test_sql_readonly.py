"""
Tests for SQL read-only enforcement in sureline/query/sandbox.py.

Covers:
- _is_read_only_sql: keyword detection, comment-stripping bypass attempts
- execute_sql: happy path, write rejection (no DB touch), bad SQL errors
"""

import sqlite3
from pathlib import Path

import pytest

from sureline.query.sandbox import _is_read_only_sql, execute_sql, SQL_WRITE_KEYWORDS


# ─── _is_read_only_sql ──────────────────────────────────────────

class TestIsReadOnlySql:
    def test_simple_select_allowed(self):
        assert _is_read_only_sql("SELECT * FROM customers") is True

    def test_select_with_where_allowed(self):
        assert _is_read_only_sql("SELECT name FROM customers WHERE id = 1") is True

    def test_select_with_join_allowed(self):
        sql = "SELECT c.name, o.amount FROM customers c JOIN orders o ON c.id = o.customer_id"
        assert _is_read_only_sql(sql) is True

    @pytest.mark.parametrize("keyword", sorted(SQL_WRITE_KEYWORDS))
    def test_write_keyword_rejected(self, keyword: str):
        sql = f"{keyword} INTO customers (name) VALUES ('x')"
        assert _is_read_only_sql(sql) is False

    def test_inline_comment_bypass_blocked(self):
        # "SELECT 1 -- DROP TABLE foo" should be rejected
        # Wait — the DROP is in a comment, so it SHOULD be allowed.
        # The strip removes the comment first, leaving "SELECT 1".
        assert _is_read_only_sql("SELECT 1 -- DROP TABLE foo") is True

    def test_drop_not_in_comment_rejected(self):
        assert _is_read_only_sql("DROP TABLE customers") is False

    def test_block_comment_stripped(self):
        # /* */ comment containing DROP — should be stripped, leaving SELECT
        assert _is_read_only_sql("SELECT 1 /* DROP TABLE x */") is True

    def test_drop_outside_comment_rejected(self):
        assert _is_read_only_sql("SELECT 1; DROP TABLE customers") is False

    def test_case_insensitive_rejection(self):
        assert _is_read_only_sql("insert into customers values (1, 'x', 'y', 0)") is False

    def test_mixed_case_rejected(self):
        assert _is_read_only_sql("DeLeTe FROM customers WHERE id = 1") is False

    def test_pragma_rejected(self):
        assert _is_read_only_sql("PRAGMA table_info(customers)") is False

    def test_attach_rejected(self):
        assert _is_read_only_sql("ATTACH DATABASE '/tmp/evil.db' AS evil") is False


# ─── execute_sql ────────────────────────────────────────────────

class TestExecuteSql:
    def test_select_returns_rows(self, db_path: Path):
        result = execute_sql(db_path, "SELECT * FROM customers ORDER BY id")
        assert result.success is True
        assert result.query_type == "sql"
        assert result.row_count == 3
        assert result.columns == ["id", "name", "account_no", "balance"]
        assert result.data[0]["name"] == "Alice"

    def test_select_with_where(self, db_path: Path):
        result = execute_sql(db_path, "SELECT name FROM customers WHERE account_no = 'ACC002'")
        assert result.success is True
        assert result.row_count == 1
        assert result.data[0]["name"] == "Bob"

    def test_empty_result_succeeds(self, db_path: Path):
        result = execute_sql(db_path, "SELECT * FROM customers WHERE id = 9999")
        assert result.success is True
        assert result.row_count == 0
        assert result.data == []

    def test_write_sql_rejected_before_execution(self, db_path: Path):
        result = execute_sql(db_path, "INSERT INTO customers VALUES (99, 'Evil', 'BAD', 0)")
        assert result.success is False
        assert "read-only" in result.error.lower()
        # Verify the DB was NOT modified (rejection happens before DB touch)
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT COUNT(*) FROM customers").fetchone()
        conn.close()
        assert row[0] == 3

    def test_drop_table_rejected(self, db_path: Path):
        result = execute_sql(db_path, "DROP TABLE customers")
        assert result.success is False

    def test_invalid_sql_returns_error(self, db_path: Path):
        result = execute_sql(db_path, "SELECT * FROM nonexistent_table")
        assert result.success is False
        assert result.error is not None

    def test_syntax_error_returns_error(self, db_path: Path):
        result = execute_sql(db_path, "SELECT FROM WHERE")
        assert result.success is False
        assert result.error is not None

    def test_generated_query_stored(self, db_path: Path):
        sql = "SELECT COUNT(*) AS total FROM customers"
        result = execute_sql(db_path, sql)
        assert result.generated_query == sql

    def test_execution_time_recorded(self, db_path: Path):
        result = execute_sql(db_path, "SELECT 1")
        assert result.execution_time_ms >= 0

    def test_nonexistent_db_returns_error(self, tmp_path: Path):
        result = execute_sql(tmp_path / "ghost.db", "SELECT 1")
        assert result.success is False
        assert result.error is not None
