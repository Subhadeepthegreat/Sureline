"""
Shared pytest fixtures for Sureline test suite.
"""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from sureline.schema_registry import (
    ClientConfig,
    CallerVerificationConfig,
    FallbackConfig,
)


# ─── SQLite fixture ──────────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create a minimal SQLite database with a customers table."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            account_no TEXT,
            balance REAL
        );
        INSERT INTO customers VALUES (1, 'Alice', 'ACC001', 1500.00);
        INSERT INTO customers VALUES (2, 'Bob',   'ACC002', 2300.50);
        INSERT INTO customers VALUES (3, 'Carol', 'ACC003',  800.75);
    """)
    conn.commit()
    conn.close()
    return path


# ─── Pandas DataFrame fixture ────────────────────────────────────

@pytest.fixture()
def sales_df() -> pd.DataFrame:
    """Minimal sales DataFrame used as cached_df in pandas sandbox tests."""
    return pd.DataFrame({
        "product": ["Widget A", "Widget B", "Widget A", "Widget C"],
        "quantity": [10, 5, 8, 3],
        "price": [9.99, 19.99, 9.99, 4.99],
        "region": ["North", "South", "North", "East"],
    })


# ─── ClientConfig fixture ────────────────────────────────────────

@pytest.fixture()
def client_config(db_path: Path) -> ClientConfig:
    """Minimal ClientConfig pointing at the test SQLite database."""
    return ClientConfig(
        client_id="test-client",
        client_name="Test Corp",
        company_description="A test company.",
        database_type="sqlite",
        database_path=str(db_path),
        caller_verification=CallerVerificationConfig(method="pin", field="account_no"),
        fallback=FallbackConfig(
            message="Please hold while we transfer you.",
            action="sip_transfer",
            target="",
        ),
    )
