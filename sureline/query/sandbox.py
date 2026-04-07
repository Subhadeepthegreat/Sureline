"""
Sureline — Sandboxed Query Execution

Executes generated SQL or Pandas code in a restricted environment.
Safety features:
  - SQL: read-only (no INSERT/UPDATE/DELETE/DROP/ALTER); comment-stripped before token check
  - Pandas: RestrictedPython bytecode sandbox + allowlist pd proxy (default deny)
  - Timeout: all queries capped at QUERY_TIMEOUT seconds
  - Results: capped at 50 rows to avoid LLM context overflow
"""

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from sureline.config import QUERY_TIMEOUT

# ─── Safe pandas adapter ─────────────────────────────────────────
# RestrictedPython blocks `import` at the bytecode level but does NOT
# prevent calling I/O methods on objects in trusted globals (e.g. pd.read_csv).
# A blocklist is insufficient because pd.io.parsers.readers.read_csv() bypasses
# name-level checks via submodule attribute chaining. Use an allowlist instead:
# only expose the DataFrame/Series transform names that LLM-generated code needs.
_PANDAS_ALLOWED = frozenset({
    # Core types
    "DataFrame", "Series", "Index", "MultiIndex",
    "Categorical", "CategoricalDtype", "Timestamp", "Timedelta", "NaT", "NA",
    # Constructors / combiners
    "concat", "merge", "merge_asof", "merge_ordered",
    "pivot", "pivot_table", "crosstab", "cut", "qcut", "melt",
    "wide_to_long", "get_dummies",
    # Type coercion
    "to_numeric", "to_datetime", "to_timedelta",
    # Misc utilities safe for analysis
    "isna", "isnull", "notna", "notnull",
    "date_range", "bdate_range", "period_range", "timedelta_range",
    "options", "set_option", "reset_option",
    "NamedAgg",
})


class _SafePandas:
    """Allowlist proxy — only exposes DataFrame transform names to sandboxed code."""

    def __getattr__(self, name: str):
        if name not in _PANDAS_ALLOWED:
            raise AttributeError(
                f"pd.{name} is not available in sandboxed code. "
                "Use the pre-loaded `df` DataFrame instead."
            )
        return getattr(pd, name)


# ─── Sandboxed attribute guard ────────────────────────────────────
# _GETATTR_BLOCKED covers I/O method names on ANY object — not just pd.xxx.
# Without this, df.to_csv('/path') bypasses _SafePandas because RestrictedPython
# transforms `df.to_csv` into `_getattr_(df, 'to_csv')`, and if _getattr_ is just
# a transparent `getattr`, the DataFrame I/O method is reachable directly.
_GETATTR_BLOCKED = frozenset({
    "to_csv", "to_json", "to_excel", "to_parquet", "to_sql",
    "to_hdf", "to_feather", "to_pickle", "to_stata", "to_gbq",
    "to_clipboard", "to_latex", "to_markdown", "to_html",
    "to_xml", "to_orc",
})


def _sandboxed_getattr(obj, name: str):
    """Custom _getattr_ guard — blocks I/O method names on any object."""
    if name in _GETATTR_BLOCKED:
        raise AttributeError(
            f"'{name}' is not available in sandboxed code."
        )
    return getattr(obj, name)


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
    import re
    # Strip single-line (--) and block (/* */) comments before tokenising
    # so that tricks like "SELECT 1 -- DROP TABLE foo" don't bypass the check
    no_comments = re.sub(r"--[^\n]*", " ", sql)
    no_comments = re.sub(r"/\*.*?\*/", " ", no_comments, flags=re.DOTALL)
    tokens = no_comments.upper().split()
    for keyword in SQL_WRITE_KEYWORDS:
        if keyword in tokens:
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
        # Open in read-only mode using URI.
        # check_same_thread=False: the connection is used across threads below
        # (run_query thread uses the cursor). Safe here because mode=ro means
        # no writes can occur, and SQLite handles concurrent reads correctly.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
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


def execute_pandas(csv_path: Path, code: str, cached_df=None) -> QueryResult:
    """
    Execute Pandas code in a sandboxed namespace.

    The code must produce a variable called `result` which should be
    a DataFrame or a scalar value.

    Args:
        csv_path: Path to the CSV file (used only when cached_df is None).
        code: Python code to execute (using pandas as pd).
        cached_df: Pre-loaded DataFrame. When provided, skips pd.read_csv()
                   on the hot path (major latency saving for repeated calls).

    Returns:
        QueryResult with data or error.
    """
    import time
    from RestrictedPython import compile_restricted, safe_globals, safe_builtins
    from RestrictedPython.Eval import default_guarded_getitem

    start = time.time()

    try:
        # Compile at bytecode level — RestrictedPython rejects any import,
        # attribute access, or dunder that isn't explicitly allowed.
        byte_code = compile_restricted(code, filename="<llm_generated>", mode="exec")

        df = cached_df if cached_df is not None else pd.read_csv(csv_path)
        restricted_globals = {
            **safe_globals,
            "__builtins__": safe_builtins,
            "pd": _SafePandas(),   # I/O methods blocked; DataFrame ops allowed
            "df": df,
            # RestrictedPython transforms df['col'] → _getitem_(df, 'col')
            # and df.method()   → _getattr_(df, 'method') — both must be provided.
            "_getitem_": default_guarded_getitem,
            "_getattr_": _sandboxed_getattr,
        }

        exec(byte_code, restricted_globals)
        namespace = restricted_globals

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
