"""Shared fixtures.

Two things every test here needs.

`isolate_settings` restores the module-level ``settings`` singleton after each
test, so a test that points ``project_root`` at a tmp_path cannot leak that into
the next one. Several tests used to depend on that leak.

`duckdb` is a real in-memory database. It replaces the FakeConn/FakeCursor/
FakeSQL doubles that had already drifted apart from each other and from the
driver they imitated. DuckDB starts in microseconds and actually parses SQL,
which means the tests now check behaviour instead of a mock's bookkeeping.
"""

from pathlib import Path

import duckdb
import pytest

from src import db
from src.settings import settings


@pytest.fixture(autouse=True)
def isolate_settings():
    snapshot = dict(settings.__dict__)
    yield
    settings.__dict__.clear()
    settings.__dict__.update(snapshot)


@pytest.fixture
def duckdb_con():
    """An in-memory database with the project schema applied."""
    schema = (Path(__file__).resolve().parents[1] / "src" / "schema.sql").read_text(
        encoding="utf-8"
    )
    con = duckdb.connect()
    con.execute(schema)
    yield con
    con.close()


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    """Point the whole project layout at a tmp_path and create the directories."""
    settings.project_root = Path(tmp_path)
    settings.duckdb_path = "data/test.duckdb"
    settings.allow_drop = True
    settings.enable_quality_gates = False
    settings.strict_fk_validation = False
    settings.create_dirs()
    monkeypatch.setattr(db, "connect", db.connect)
    return settings


@pytest.fixture
def empty_domain_tables():
    """Domain tables start empty, so FK validation has nothing to check against.

    With foreign keys declared in CREATE TABLE, an orphan reference is rejected
    by the engine rather than by validate_chunk, so most unit tests run against
    data whose references resolve.
    """
    settings.strict_fk_validation = False