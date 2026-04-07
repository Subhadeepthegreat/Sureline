"""
Sureline — Database Schema Loader

Introspects SQLite databases & CSV files to produce human-readable
schema descriptions for the LLM to use when generating queries.
"""

import sqlite3
import csv
from pathlib import Path
from typing import Optional

import pandas as pd


def load_sqlite_schema(db_path: Path) -> str:
    """
    Introspect a SQLite database and return a human-readable schema description.

    Returns a string like:
        Table: employees (80 rows)
        Columns:
          - id (INTEGER, PK)
          - name (TEXT)
          - department (TEXT) — values: "Rocket Propulsion", "Satellite Division", ...
          ...
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    schema_parts = []

    for table in tables:
        # Row count
        cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
        row_count = cursor.fetchone()[0]

        # Column info
        cursor.execute(f"PRAGMA table_info([{table}])")
        columns = cursor.fetchall()  # (cid, name, type, notnull, default, pk)

        col_descriptions = []
        for col in columns:
            cid, col_name, col_type, notnull, default, pk = col
            parts = [f"{col_name} ({col_type}"]
            if pk:
                parts[0] += ", PRIMARY KEY"
            parts[0] += ")"

            # Get distinct values for TEXT columns (for context)
            if col_type == "TEXT" and col_name not in ("email", "description"):
                cursor.execute(
                    f"SELECT DISTINCT [{col_name}] FROM [{table}] LIMIT 10"
                )
                distinct = [r[0] for r in cursor.fetchall() if r[0]]
                if distinct:
                    vals = ", ".join(f'"{v}"' for v in distinct[:8])
                    if len(distinct) > 8:
                        vals += ", ..."
                    parts.append(f"  values: [{vals}]")

            # Get min/max for INTEGER columns (for context)
            if col_type == "INTEGER" and not pk:
                cursor.execute(
                    f"SELECT MIN([{col_name}]), MAX([{col_name}]), AVG([{col_name}]) FROM [{table}]"
                )
                mn, mx, avg = cursor.fetchone()
                if mn is not None:
                    parts.append(f"  range: {mn:,} to {mx:,} (avg: {avg:,.0f})")

            col_descriptions.append("    - " + " — ".join(parts))

        # Foreign keys
        cursor.execute(f"PRAGMA foreign_key_list([{table}])")
        fks = cursor.fetchall()
        fk_info = ""
        if fks:
            fk_parts = [f"    FK: {fk[3]} → {fk[2]}.{fk[4]}" for fk in fks]
            fk_info = "\n" + "\n".join(fk_parts)

        schema_parts.append(
            f"Table: {table} ({row_count:,} rows)\n"
            f"  Columns:\n"
            + "\n".join(col_descriptions)
            + fk_info
        )

    conn.close()

    return "\n\n".join(schema_parts)


def load_csv_schema(csv_path: Path, sample_rows: int = 3) -> str:
    """
    Read a CSV file and return a schema description with sample rows.
    """
    df = pd.read_csv(csv_path, nrows=100)

    lines = [f"CSV File: {csv_path.name} ({len(df)} rows loaded, may have more)"]
    lines.append("Columns:")

    for col in df.columns:
        dtype = str(df[col].dtype)
        if df[col].dtype == "object":
            uniques = df[col].nunique()
            sample_vals = df[col].dropna().unique()[:5]
            vals_str = ", ".join(f'"{v}"' for v in sample_vals)
            lines.append(f"  - {col} (text, {uniques} unique) — e.g. [{vals_str}]")
        else:
            lines.append(
                f"  - {col} ({dtype}) — range: {df[col].min()} to {df[col].max()}"
            )

    lines.append(f"\nSample rows (first {sample_rows}):")
    for _, row in df.head(sample_rows).iterrows():
        lines.append(f"  {dict(row)}")

    return "\n".join(lines)


def get_full_schema(db_path: Optional[Path] = None, csv_path: Optional[Path] = None) -> str:
    """
    Get the full schema description for all available data sources.
    """
    parts = []

    if db_path and db_path.exists():
        parts.append("═══ SQLite Database ═══\n" + load_sqlite_schema(db_path))

    if csv_path and csv_path.exists():
        parts.append("═══ CSV File ═══\n" + load_csv_schema(csv_path))

    return "\n\n".join(parts) if parts else "No data sources found."


if __name__ == "__main__":
    from sureline.config import DB_PATH, DATA_DIR
    csv_path = DATA_DIR / "mahakash_sales.csv"
    print(get_full_schema(DB_PATH, csv_path))
