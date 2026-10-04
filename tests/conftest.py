"""Test setup: force a local (never remote) database, even if .env exists."""

import os

# Must happen at import time, before any db_config() call loads .env:
# empty URL keys make db_config() fall back to the local file DB.
for _key in (
    "TURSO_DATABASE_URL",
    "TURSO_URL",
    "DATABASE_URL",
    "LIBSQL_URL",
    "TURSO_AUTH_TOKEN",
    "TURSO_TOKEN",
    "AUTH_TOKEN",
    "LIBSQL_AUTH_TOKEN",
):
    os.environ[_key] = ""

import pytest  # noqa: E402

import config  # noqa: E402
import database  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A fresh local SQLite file per test, with schema initialized."""
    monkeypatch.setattr(config, "LOCAL_DB_PATH", tmp_path / "test.db")
    database.init_schema()
    return tmp_path / "test.db"


@pytest.fixture()
def conn(db):
    connection = database.connect()
    yield connection
    connection.close()
