"""
Creates devlogs.db — a SQLite database for tracking development errors and fixes.

Schema:
  dev_logs
    id             INTEGER  PRIMARY KEY
    timestamp      TEXT     ISO-8601 when the error occurred
    file_path      TEXT     which file the error came from
    error_type     TEXT     exception class name or category (e.g. ImportError, RuntimeError)
    error_message  TEXT     full error/traceback text
    resolved       INTEGER  0 = unresolved, 1 = resolved
    resolution     TEXT     how the error was fixed (filled in after mitigation)
    resolved_at    TEXT     ISO-8601 when it was marked resolved (NULL if unresolved)

Usage:
  python data/create_devlogs.py          # creates the DB
  python data/create_devlogs.py --reset  # drops and recreates
"""

import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "devlogs.db"


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dev_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    file_path       TEXT    NOT NULL DEFAULT '',
    error_type      TEXT    NOT NULL DEFAULT '',
    error_message   TEXT    NOT NULL,
    resolved        INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    resolution      TEXT             DEFAULT NULL,
    resolved_at     TEXT             DEFAULT NULL
);
"""

# Useful views for quick queries
CREATE_VIEWS_SQL = [
    """
    CREATE VIEW IF NOT EXISTS unresolved_errors AS
    SELECT id, timestamp, file_path, error_type,
           substr(error_message, 1, 120) AS error_preview
    FROM   dev_logs
    WHERE  resolved = 0
    ORDER  BY timestamp DESC;
    """,
    """
    CREATE VIEW IF NOT EXISTS resolved_errors AS
    SELECT id, timestamp, file_path, error_type,
           substr(error_message, 1, 120)  AS error_preview,
           substr(resolution, 1, 120)     AS fix_preview,
           resolved_at
    FROM   dev_logs
    WHERE  resolved = 1
    ORDER  BY resolved_at DESC;
    """,
]


def create_db(reset: bool = False) -> None:
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Dropped existing {DB_PATH.name}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(CREATE_TABLE_SQL)
    for sql in CREATE_VIEWS_SQL:
        cur.execute(sql)
    conn.commit()
    conn.close()
    print(f"devlogs.db ready at {DB_PATH}")
    print()
    print("Useful queries:")
    print("  -- log a new error")
    print("  INSERT INTO dev_logs (file_path, error_type, error_message)")
    print("  VALUES ('pipeline.py', 'ImportError', 'No module named sarvamai');")
    print()
    print("  -- mark resolved")
    print("  UPDATE dev_logs")
    print("  SET resolved=1, resolution='pip install sarvamai>=0.1.25',")
    print("      resolved_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    print("  WHERE id=1;")
    print()
    print("  -- see all unresolved")
    print("  SELECT * FROM unresolved_errors;")
    print()
    print("  -- see all resolved with fixes")
    print("  SELECT * FROM resolved_errors;")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Drop and recreate the database")
    args = parser.parse_args()
    create_db(reset=args.reset)
