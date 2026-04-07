"""
Tests for the pandas sandbox in sureline/query/sandbox.py.

Covers:
- execute_pandas: happy path (scalar, Series, DataFrame), missing result var
- _SafePandas allowlist: blocked I/O names, blocked submodule chaining (pd.io bypass)
- RestrictedPython: import blocking, __import__ blocking
- cached_df: skips disk I/O when pre-loaded DataFrame is provided
"""

from pathlib import Path

import pandas as pd
import pytest

from sureline.query.sandbox import _SafePandas, execute_pandas, _PANDAS_ALLOWED


# ─── _SafePandas allowlist ───────────────────────────────────────

class TestSafePandas:
    def setup_method(self):
        self.sp = _SafePandas()

    def test_allowed_name_passes_through(self):
        # DataFrame should resolve to pandas.DataFrame
        assert self.sp.DataFrame is pd.DataFrame

    def test_concat_allowed(self):
        assert self.sp.concat is pd.concat

    def test_merge_allowed(self):
        assert self.sp.merge is pd.merge

    def test_to_numeric_allowed(self):
        assert self.sp.to_numeric is pd.to_numeric

    def test_read_csv_blocked(self):
        with pytest.raises(AttributeError, match="not available in sandboxed code"):
            _ = self.sp.read_csv

    def test_to_csv_blocked(self):
        with pytest.raises(AttributeError, match="not available in sandboxed code"):
            _ = self.sp.to_csv

    def test_read_sql_blocked(self):
        with pytest.raises(AttributeError, match="not available in sandboxed code"):
            _ = self.sp.read_sql

    def test_to_parquet_blocked(self):
        with pytest.raises(AttributeError, match="not available in sandboxed code"):
            _ = self.sp.to_parquet

    def test_io_submodule_blocked(self):
        # Key regression: blocklist allowed `pd.io` through attribute chaining.
        # The allowlist must reject 'io' since it's not in _PANDAS_ALLOWED.
        with pytest.raises(AttributeError, match="not available in sandboxed code"):
            _ = self.sp.io

    def test_unknown_name_blocked(self):
        with pytest.raises(AttributeError):
            _ = self.sp.some_nonexistent_attr

    def test_allowed_set_is_nonempty(self):
        assert len(_PANDAS_ALLOWED) > 10


# ─── execute_pandas: happy path ──────────────────────────────────

class TestExecutePandasHappyPath:
    def test_scalar_result(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "result = len(df)"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is True
        assert res.data == 4
        assert res.query_type == "pandas"

    def test_dataframe_result(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "result = df[df['region'] == 'North']"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is True
        assert isinstance(res.data, list)
        assert all(row["region"] == "North" for row in res.data)
        assert res.row_count == 2

    def test_series_result(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "result = df.groupby('region')['quantity'].sum()"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is True
        assert isinstance(res.data, dict)

    def test_aggregation_result(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "result = df['price'].mean()"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is True
        assert isinstance(res.data, float)

    def test_generated_query_stored(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "result = df.head(1)"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.generated_query == code

    def test_execution_time_recorded(self, sales_df: pd.DataFrame, tmp_path: Path):
        res = execute_pandas(tmp_path / "unused.csv", "result = 42", cached_df=sales_df)
        assert res.execution_time_ms >= 0

    def test_cached_df_used_not_csv(self, sales_df: pd.DataFrame, tmp_path: Path):
        # The csv_path doesn't exist but cached_df is provided — should not fail
        res = execute_pandas(tmp_path / "ghost.csv", "result = len(df)", cached_df=sales_df)
        assert res.success is True
        assert res.data == len(sales_df)

    def test_result_capped_at_50_rows(self, tmp_path: Path):
        big_df = pd.DataFrame({"x": range(200)})
        code = "result = df"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=big_df)
        assert res.success is True
        assert res.row_count <= 50


# ─── execute_pandas: error cases ─────────────────────────────────

class TestExecutePandasErrors:
    def test_missing_result_var(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "x = df.head(1)"  # forgot to assign to 'result'
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is False
        assert "result" in res.error.lower()

    def test_import_blocked_by_restrictedpython(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "import os; result = os.getcwd()"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is False

    def test_dunder_import_blocked(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "os = __import__('os'); result = os.getcwd()"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is False

    def test_pd_read_csv_blocked_in_exec(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "result = pd.read_csv('/etc/passwd')"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is False

    def test_pd_io_submodule_blocked_in_exec(self, sales_df: pd.DataFrame, tmp_path: Path):
        # The allowlist regression: pd.io must not be accessible
        code = "result = pd.io.parsers.readers.read_csv('/etc/passwd')"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is False

    def test_open_builtin_blocked(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "f = open('/etc/passwd'); result = f.read()"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is False

    def test_df_to_csv_blocked_via_getattr(self, sales_df: pd.DataFrame, tmp_path: Path):
        # Regression: df.to_csv('/path') bypasses _SafePandas because RestrictedPython
        # transforms it into _getattr_(df, 'to_csv'), not _getattr_(pd, 'to_csv').
        # _sandboxed_getattr must block I/O method names on any object, not just pd.
        code = "result = df.to_csv('/tmp/exfil.csv')"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is False

    def test_df_to_json_blocked_via_getattr(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "result = df.to_json('/tmp/exfil.json')"
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is False

    def test_syntax_error_returns_failure(self, sales_df: pd.DataFrame, tmp_path: Path):
        code = "result = df[["  # malformed
        res = execute_pandas(tmp_path / "unused.csv", code, cached_df=sales_df)
        assert res.success is False
        assert res.error is not None
