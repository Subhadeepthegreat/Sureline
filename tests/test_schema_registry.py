"""
Tests for sureline/schema_registry.py::SchemaRegistry.

Covers:
- Valid YAML loads into a correct ClientConfig
- Caching: second load() call returns the same object (no re-parse)
- Invalid client_id (path traversal, bad chars, too long) → ValueError
- Missing YAML → FileNotFoundError
- Malformed YAML → ValueError
- Defaults populated correctly when optional fields absent
"""

from pathlib import Path

import pytest
import yaml

from sureline.schema_registry import SchemaRegistry, ClientConfig


# ─── Helpers ─────────────────────────────────────────────────────

def _write_yaml(clients_dir: Path, client_id: str, data: dict) -> Path:
    path = clients_dir / f"{client_id}.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


MINIMAL_YAML = {
    "client_id": "acme-bank",
    "client_name": "ACME Bank",
    "company_description": "A test bank.",
    "database": {"type": "sqlite", "path": "/data/acme.db"},
}

FULL_YAML = {
    **MINIMAL_YAML,
    "nl_queries_enabled": True,
    "caller_verification": {"method": "pin", "field": "pin_code"},
    "fallback": {
        "message": "Transferring now.",
        "action": "sip_transfer",
        "target": "sip:support@bank.com",
    },
    "templated_queries": [
        {
            "template": "What is my balance?",
            "sql": "SELECT balance FROM customers WHERE account_no = :account_no",
            "spoken": "Your balance is {balance}.",
        }
    ],
    "golden_test_suite": [
        {
            "input": "What is my balance?",
            "expected_sql_contains": "balance",
            "expected_spoken_contains": "balance",
        }
    ],
}


# ─── Valid YAML loading ───────────────────────────────────────────

class TestSchemaRegistryLoad:
    def test_minimal_yaml_loads(self, tmp_path: Path):
        _write_yaml(tmp_path, "acme-bank", MINIMAL_YAML)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config = registry.load("acme-bank")
        assert isinstance(config, ClientConfig)
        assert config.client_id == "acme-bank"
        assert config.client_name == "ACME Bank"
        assert config.database_type == "sqlite"
        assert config.database_path == "/data/acme.db"

    def test_full_yaml_loads(self, tmp_path: Path):
        _write_yaml(tmp_path, "acme-bank", FULL_YAML)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config = registry.load("acme-bank")
        assert config.nl_queries_enabled is True
        assert config.caller_verification.method == "pin"
        assert config.caller_verification.field == "pin_code"
        assert config.fallback.message == "Transferring now."
        assert config.fallback.target == "sip:support@bank.com"
        assert len(config.templated_queries) == 1
        assert config.templated_queries[0].sql.startswith("SELECT balance")
        assert len(config.golden_test_suite) == 1
        assert config.golden_test_suite[0].expected_sql_contains == "balance"

    def test_defaults_populated_when_optional_fields_absent(self, tmp_path: Path):
        _write_yaml(tmp_path, "acme-bank", MINIMAL_YAML)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config = registry.load("acme-bank")
        # Defaults from dataclass
        assert config.caller_verification.method == "pin"
        assert config.caller_verification.field == "account_no"
        assert "transfer" in config.fallback.message.lower()
        assert config.nl_queries_enabled is False
        assert config.templated_queries == []
        assert config.golden_test_suite == []

    def test_client_id_falls_back_to_filename(self, tmp_path: Path):
        # client_id omitted from YAML — should use filename stem
        data = {k: v for k, v in MINIMAL_YAML.items() if k != "client_id"}
        _write_yaml(tmp_path, "acme-bank", data)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config = registry.load("acme-bank")
        assert config.client_id == "acme-bank"


# ─── Caching ─────────────────────────────────────────────────────

class TestSchemaRegistryCache:
    def test_second_load_returns_same_object(self, tmp_path: Path):
        _write_yaml(tmp_path, "acme-bank", MINIMAL_YAML)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config1 = registry.load("acme-bank")
        config2 = registry.load("acme-bank")
        assert config1 is config2

    def test_different_clients_independent(self, tmp_path: Path):
        _write_yaml(tmp_path, "acme-bank", MINIMAL_YAML)
        data2 = {**MINIMAL_YAML, "client_id": "beta-corp", "client_name": "Beta Corp"}
        _write_yaml(tmp_path, "beta-corp", data2)
        registry = SchemaRegistry(clients_dir=tmp_path)
        c1 = registry.load("acme-bank")
        c2 = registry.load("beta-corp")
        assert c1.client_name == "ACME Bank"
        assert c2.client_name == "Beta Corp"


# ─── Invalid client_id ────────────────────────────────────────────

class TestSchemaRegistryInvalidClientId:
    @pytest.mark.parametrize("bad_id", [
        "../etc/passwd",
        "../../etc",
        "/absolute/path",
        "",
        "has space",
        "has/slash",
        "a" * 65,           # too long (max 64)
        "has.dot",
    ])
    def test_invalid_client_id_raises_value_error(self, tmp_path: Path, bad_id: str):
        registry = SchemaRegistry(clients_dir=tmp_path)
        with pytest.raises((ValueError, FileNotFoundError)):
            registry.load(bad_id)

    @pytest.mark.parametrize("good_id", [
        "acme-bank",
        "client_001",
        "ABC123",
        "a-b-c",
        "a" * 64,           # exactly max length
    ])
    def test_valid_client_id_format_accepted(self, tmp_path: Path, good_id: str):
        _write_yaml(tmp_path, good_id, MINIMAL_YAML)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config = registry.load(good_id)
        assert config is not None


# ─── Error cases ─────────────────────────────────────────────────

class TestSchemaRegistryErrors:
    def test_missing_yaml_raises_file_not_found(self, tmp_path: Path):
        registry = SchemaRegistry(clients_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            registry.load("no-such-client")

    def test_malformed_yaml_raises_value_error(self, tmp_path: Path):
        path = tmp_path / "bad-client.yaml"
        path.write_text(": invalid: yaml: content: [[[", encoding="utf-8")
        registry = SchemaRegistry(clients_dir=tmp_path)
        with pytest.raises(ValueError, match="Invalid YAML"):
            registry.load("bad-client")


# ─── database_path extension validation ──────────────────────────

class TestDatabasePathValidation:
    @pytest.mark.parametrize("db_path", [
        "/data/customers.db",
        "/data/customers.sqlite",
        "/data/customers.sqlite3",
    ])
    def test_sqlite_valid_extensions_accepted(self, tmp_path: Path, db_path: str):
        data = {**MINIMAL_YAML, "database": {"type": "sqlite", "path": db_path}}
        _write_yaml(tmp_path, "acme-bank", data)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config = registry.load("acme-bank")
        assert config.database_path == db_path

    @pytest.mark.parametrize("bad_path", [
        "/data/customers.json",
        "/data/customers.csv",
        "/data/customers",
        "/data/customers.txt",
    ])
    def test_sqlite_wrong_extension_raises(self, tmp_path: Path, bad_path: str):
        data = {**MINIMAL_YAML, "database": {"type": "sqlite", "path": bad_path}}
        _write_yaml(tmp_path, "acme-bank", data)
        registry = SchemaRegistry(clients_dir=tmp_path)
        with pytest.raises(ValueError, match="extension"):
            registry.load("acme-bank")

    def test_csv_type_with_csv_path_accepted(self, tmp_path: Path):
        data = {**MINIMAL_YAML, "database": {"type": "csv", "path": "/data/sales.csv"}}
        _write_yaml(tmp_path, "acme-bank", data)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config = registry.load("acme-bank")
        assert config.database_path == "/data/sales.csv"

    def test_csv_type_with_db_extension_raises(self, tmp_path: Path):
        data = {**MINIMAL_YAML, "database": {"type": "csv", "path": "/data/sales.db"}}
        _write_yaml(tmp_path, "acme-bank", data)
        registry = SchemaRegistry(clients_dir=tmp_path)
        with pytest.raises(ValueError, match="extension"):
            registry.load("acme-bank")

    def test_postgres_type_skips_extension_check(self, tmp_path: Path):
        # postgres uses host:port, no meaningful extension
        data = {**MINIMAL_YAML, "database": {"type": "postgres", "path": "db.example.com:5432"}}
        _write_yaml(tmp_path, "acme-bank", data)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config = registry.load("acme-bank")
        assert config.database_type == "postgres"

    def test_empty_path_skips_validation(self, tmp_path: Path):
        data = {**MINIMAL_YAML, "database": {"type": "sqlite", "path": ""}}
        _write_yaml(tmp_path, "acme-bank", data)
        registry = SchemaRegistry(clients_dir=tmp_path)
        config = registry.load("acme-bank")
        assert config.database_path == ""
