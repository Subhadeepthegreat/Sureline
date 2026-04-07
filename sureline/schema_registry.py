"""
Sureline — Schema Registry

Loads per-client YAML configuration and serves it to QueryEngine,
ConversationEngine, and CallerVerificationProcessor.

Each client gets a YAML file that describes their database, schema
annotations, templated queries, and caller verification settings.
New client = new YAML file. No code changes needed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from sureline.config import PROJECT_ROOT

_SAFE_CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

logger = logging.getLogger(__name__)

# Default client configs live here; can be overridden per-deployment
CLIENTS_DIR: Path = PROJECT_ROOT / "clients"


@dataclass
class CallerVerificationConfig:
    method: str = "pin"          # "pin" | "ani" | "otp"
    field: str = "account_no"   # DB column to verify against


@dataclass
class FallbackConfig:
    message: str = "I'll transfer you to our team for that."
    action: str = "sip_transfer"   # "sip_transfer" | "webhook"
    target: str = ""


@dataclass
class GoldenTest:
    input: str
    expected_sql_contains: str = ""
    expected_spoken_contains: str = ""


@dataclass
class TemplatedQuery:
    template: str
    sql: str
    spoken: str


@dataclass
class ClientConfig:
    client_id: str
    client_name: str
    company_description: str
    database_type: str                          # "sqlite" | "postgres" | "csv"
    database_path: str                          # path or host:port
    schema_annotations: dict[str, Any] = field(default_factory=dict)
    templated_queries: list[TemplatedQuery] = field(default_factory=list)
    nl_queries_enabled: bool = False
    caller_verification: CallerVerificationConfig = field(
        default_factory=CallerVerificationConfig
    )
    fallback: FallbackConfig = field(default_factory=FallbackConfig)
    golden_test_suite: list[GoldenTest] = field(default_factory=list)


class SchemaRegistry:
    """
    Loads and serves per-client configuration from YAML files.

    Usage:
        registry = SchemaRegistry()
        config = registry.load("acme-bank")
        # config.client_name, config.database_path, etc.
    """

    def __init__(self, clients_dir: Optional[Path] = None):
        self._dir = clients_dir or CLIENTS_DIR
        self._cache: dict[str, ClientConfig] = {}

    def load(self, client_id: str) -> ClientConfig:
        """Load a client config by ID. Raises ValueError on bad YAML or client_id."""
        if not _SAFE_CLIENT_ID_RE.match(client_id):
            raise ValueError(
                f"Invalid client_id '{client_id}': must match ^[a-zA-Z0-9_-]{{1,64}}$"
            )

        if client_id in self._cache:
            return self._cache[client_id]

        path = self._dir / f"{client_id}.yaml"
        # Confirm the resolved path stays inside CLIENTS_DIR (path traversal guard)
        try:
            path.resolve().relative_to(self._dir.resolve())
        except ValueError:
            raise ValueError(f"client_id '{client_id}' resolves outside clients directory")

        if not path.exists():
            raise FileNotFoundError(
                f"No client config found for '{client_id}' at {path}"
            )

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(
                f"Invalid YAML in client config {path.name}: {exc}"
            ) from exc

        config = self._parse(raw, path)
        self._cache[client_id] = config
        logger.info("Loaded client config: %s (%s)", client_id, config.client_name)
        return config

    _SQLITE_EXTENSIONS = frozenset({".db", ".sqlite", ".sqlite3"})
    _CSV_EXTENSIONS = frozenset({".csv"})

    def _validate_database_path(self, db_type: str, db_path: str) -> None:
        """Validate database path extension matches declared type."""
        if not db_path:
            return  # Empty path: will fail at runtime (acceptable for partial configs)
        suffix = Path(db_path).suffix.lower()
        if db_type == "sqlite" and suffix not in self._SQLITE_EXTENSIONS:
            raise ValueError(
                f"database.path '{db_path}' has extension '{suffix}' but type is 'sqlite'. "
                f"Expected one of: {sorted(self._SQLITE_EXTENSIONS)}"
            )
        if db_type == "csv" and suffix not in self._CSV_EXTENSIONS:
            raise ValueError(
                f"database.path '{db_path}' has extension '{suffix}' but type is 'csv'. "
                f"Expected: .csv"
            )

    def _parse(self, raw: dict, path: Path) -> ClientConfig:
        db = raw.get("database", {})
        cv_raw = raw.get("caller_verification", {})
        fb_raw = raw.get("fallback", {})

        templated = [
            TemplatedQuery(
                template=q["template"],
                sql=q["sql"],
                spoken=q["spoken"],
            )
            for q in raw.get("templated_queries", [])
        ]

        golden = [
            GoldenTest(
                input=t["input"],
                expected_sql_contains=t.get("expected_sql_contains", ""),
                expected_spoken_contains=t.get("expected_spoken_contains", ""),
            )
            for t in raw.get("golden_test_suite", [])
        ]

        db_type = db.get("type", "sqlite")
        db_path_str = db.get("path", "")
        self._validate_database_path(db_type, db_path_str)

        return ClientConfig(
            client_id=raw.get("client_id", path.stem),
            client_name=raw.get("client_name", raw.get("client_id", path.stem)),
            company_description=raw.get("company_description", ""),
            database_type=db_type,
            database_path=db_path_str,
            schema_annotations=raw.get("schema_annotations", {}),
            templated_queries=templated,
            nl_queries_enabled=raw.get("nl_queries_enabled", False),
            caller_verification=CallerVerificationConfig(
                method=cv_raw.get("method", "pin"),
                field=cv_raw.get("field", "account_no"),
            ),
            fallback=FallbackConfig(
                message=fb_raw.get("message", "I'll transfer you to our team for that."),
                action=fb_raw.get("action", "sip_transfer"),
                target=fb_raw.get("target", ""),
            ),
            golden_test_suite=golden,
        )
