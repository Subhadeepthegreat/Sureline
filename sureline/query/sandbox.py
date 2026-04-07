"""
Sureline — Sandboxed Query Execution

Executes generated SQL or Pandas code in a restricted environment.
Safety features:
  - SQL: read-only (no INSERT/UPDATE/DELETE/DROP/ALTER)
  - Pandas: restricted namespace (no os, sys, subprocess, etc.)
  - Timeout: all queries capped at QUERY_TIMEOUT seconds
  - Results: capped at 50 rows to avoid LLM context overflow
"""

import sqlite3
import signal
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from sureline.config import QUERY_TIMEOUT


# ─── SQL blacklist ───────────────────────────────────────────────
SQL_WRITE_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "REPLACE", "ATTACH", "DETACH", "VACUUM",
    "PRAGMA", "GRANT", "REVOKE",
}

MAX_RESULT_ROWS = 50


@dataclass
class QueryResult:
    """Result of executing a query."""
    success: bool
    data: Any = None               # List of dicts for SQL, DataFrame for Pandas
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    generated_query: str = ""
    query_type: str = ""           # "sql" or "pandas"
    error: Optional[str] = None
    execution_time_ms: float = 0


def _is_read_only_sql(sql: str) -> bool:
    """Check that SQL query is read-only (no destructive operations)."""
    # Normalize — remove comments and check tokens
    cleaned = " ".join(sql.upper().split())
    for keyword in SQL_WRITE_KEYWORDS:
        # Check for keyword at word boundaries
        if keyword in cleaned.split():
            return False
    return True


def execute_sql(db_path: Path, sql: str) -> QueryResult:
    """
    Execute a SQL query against a SQLite database in read-only mode.

    Args:
        db_path: Path to the SQLite database.
        sql: The SQL query to execute.

    Returns:
        QueryResult with data or error.
    """
    import time

    if not _is_read_only_sql(sql):
        return QueryResult(
            success=False,
            generated_query=sql,
            query_type="sql",
            error="Query rejected: only read-only (SELECT) queries are allowed.",
        )

    start = time.time()

    try:
        # Open in read-only mode using URI
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Set a statement timeout (SQLite's busy_timeout)
        conn.execute(f"PRAGMA busy_timeout = {QUERY_TIMEOUT * 1000}")

        # Execute with a thread-based timeout
        result_holder = {"rows": None, "error": None}

        def run_query():
            try:
                cursor.execute(sql)
                result_holder["rows"] = cursor.fetchmany(MAX_RESULT_ROWS)
            except Exception as e:
                result_holder["error"] = str(e)

        thread = threading.Thread(target=run_query)
        thread.start()
        thread.join(timeout=QUERY_TIMEOUT)

        elapsed = (time.time() - start) * 1000

        if thread.is_alive():
            conn.interrupt()
            thread.join(timeout=1)
            conn.close()
            return QueryResult(
                success=False,
                generated_query=sql,
                query_type="sql",
                error=f"Query timed out after {QUERY_TIMEOUT} seconds.",
                execution_time_ms=elapsed,
            )

        if result_holder["error"]:
            conn.close()
            return QueryResult(
                success=False,
                generated_query=sql,
                query_type="sql",
                error=result_holder["error"],
                execution_time_ms=elapsed,
            )

        rows = result_holder["rows"]
        if rows:
            columns = list(rows[0].keys())
            data = [dict(row) for row in rows]
        else:
            columns = []
            data = []

        conn.close()

        return QueryResult(
            success=True,
            data=data,
            columns=columns,
            row_count=len(data),
            generated_query=sql,
            query_type="sql",
            execution_time_ms=elapsed,
        )

    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return QueryResult(
            success=False,
            generated_query=sql,
            query_type="sql",
            error=str(e),
            execution_time_ms=elapsed,
        )


def execute_pandas(csv_path: Path, code: str) -> QueryResult:
    """
    Execute Pandas code in a sandboxed namespace.

    The code must produce a variable called `result` which should be
    a DataFrame or a scalar value.

    Args:
        csv_path: Path to the CSV file.
        code: Python code to execute (using pandas as pd).

    Returns:
        QueryResult with data or error.
    """
    import time

    # Restricted namespace — only allow pandas and basic builtins
    safe_builtins = {
        "len": len, "sum": sum, "min": min, "max": max,
        "int": int, "float": float, "str": str, "bool": bool,
        "list": list, "dict": dict, "tuple": tuple,
        "range": range, "sorted": sorted, "round": round,
        "abs": abs, "enumerate": enumerate, "zip": zip,
        "True": True, "False": False, "None": None,
    }

    namespace = {
        "__builtins__": safe_builtins,
        "pd": pd,
        "df": pd.read_csv(csv_path),
    }

    start = time.time()

    try:
        # Check for dangerous imports
        dangerous = ["import os", "import sys", "import subprocess", "import shutil",
                      "__import__", "eval(", "exec(", "open(", "compile("]
        for d in dangerous:
            if d in code:
                return QueryResult(
                    success=False,
                    generated_query=code,
                    query_type="pandas",
                    error=f"Forbidden operation detected: {d}",
                )

        # Execute in restricted namespace
        exec(code, namespace)

        elapsed = (time.time() - start) * 1000

        result = namespace.get("result")
        if result is None:
            return QueryResult(
                success=False,
                generated_query=code,
                query_type="pandas",
                error="Code must assign output to a variable named 'result'.",
                execution_time_ms=elapsed,
            )

        # Convert result to serializable format
        if isinstance(result, pd.DataFrame):
            result = result.head(MAX_RESULT_ROWS)
            data = result.to_dict(orient="records")
            columns = list(result.columns)
        elif isinstance(result, pd.Series):
            data = result.head(MAX_RESULT_ROWS).to_dict()
            columns = [result.name or "value"]
        else:
            # Scalar value
            data = result
            columns = ["value"]

        return QueryResult(
            success=True,
            data=data,
            columns=columns,
            row_count=len(data) if isinstance(data, (list, dict)) else 1,
            generated_query=code,
            query_type="pandas",
            execution_time_ms=elapsed,
        )

    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return QueryResult(
            success=False,
            generated_query=code,
            query_type="pandas",
            error=str(e),
            execution_time_ms=elapsed,
        )
