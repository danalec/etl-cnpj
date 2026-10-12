"""DuckDB-native load path: parity with the pandas one.

Tables with nothing to clean and nothing to validate are read, cast, deduplicated
and written entirely inside DuckDB. That is only worth doing if the result is
indistinguishable from the pandas path it replaces, so most of what is asserted
here is parity rather than the new behaviour itself.

Two findings from building it are pinned by tests rather than by comments,
because both fail silently:

- the UTF-8 BOM read as latin-1 arrives as three characters, not as U+FEFF, so
  stripping with the obvious predicate only pollutes the first value;
- ``rel.sql_query()`` and ``rel.select()`` on a ``read_csv`` relation force the
  whole file through the client, so the source SELECT is composed as text.
"""

import json

import pytest

from src import db
from src.database_loader import ETL_CONFIG, _native_eligible, process_and_load_file
from src.settings import settings

DOMAIN = ["paises", "municipios", "qualificacoes", "naturezas", "cnaes"]
VALIDATED = ["empresas", "estabelecimentos", "socios", "simples"]


def _write(config_key: str, volume: int, rows: list[str], bom: bool = False):
    d = settings.extracted_dir / config_key
    d.mkdir(parents=True, exist_ok=True)
    body = "\n".join(rows)
    # Written as bytes on purpose: a UTF-8 BOM is not representable in the latin-1
    # text encoding the volumes use.
    (d / f"{config_key}{volume}.csv").write_bytes(
        (b"\xef\xbb\xbf" if bom else b"") + body.encode("latin-1")
    )


def _load(only: str, monkeypatch, *, native: bool):
    import src.database_loader as dl

    monkeypatch.setattr(dl, "_native_eligible", lambda *a, **k: native)
    conn = db.connect(settings.duckdb_file)
    try:
        dl.execute_sql_file(conn, "schema.sql")
        process_and_load_file(conn, only)
    finally:
        conn.close()


def _rows(db_file, table):
    conn = db.connect(db_file, read_only=True)
    try:
        return conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
    finally:
        conn.close()


def _summary(config_key):
    base = settings.telemetry_dir
    for day in sorted(base.iterdir(), reverse=True):
        f = day / f"{config_key}_summary.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    raise AssertionError(f"sem resumo para {config_key}")


# --- roteamento -----------------------------------------------------------


def test_domain_tables_take_the_native_path():
    for name in DOMAIN:
        assert _native_eligible(name, ETL_CONFIG[name]), name


def test_validated_tables_stay_on_pandas():
    """A schema or a cleaner means a DataFrame is still required."""
    for name in VALIDATED:
        assert not _native_eligible(name, ETL_CONFIG[name]), name


def test_a_new_schema_disqualifies_a_table_without_touching_a_flag():
    """Eligibility is derived, so validation cannot be skipped by forgetting a box."""
    from src.validation import SCHEMA_MAP

    SCHEMA_MAP["paises"] = object
    try:
        assert not _native_eligible("paises", ETL_CONFIG["paises"])
    finally:
        del SCHEMA_MAP["paises"]


# --- paridade -------------------------------------------------------------


def test_native_and_pandas_produce_the_same_rows(tmp_project, monkeypatch):
    rows = ["150;Brasil", "160;Portugal", "170;Angola"]
    _write("paises", 1, rows)
    _load("paises", monkeypatch, native=True)
    native = _rows(settings.duckdb_file, "paises")

    db_path = settings.duckdb_file
    db_path.unlink()
    _write("paises", 1, rows)
    _load("paises", monkeypatch, native=False)
    pandas_rows = _rows(db_path, "paises")

    assert native == pandas_rows
    assert native != []


def test_both_paths_count_the_same_rows(tmp_project, monkeypatch):
    _write("paises", 1, ["150;Brasil", "160;Portugal"])
    _load("paises", monkeypatch, native=True)
    native = _summary("paises")

    settings.duckdb_file.unlink()
    _write("paises", 1, ["150;Brasil", "160;Portugal"])
    _load("paises", monkeypatch, native=False)
    pandas_summary = _summary("paises")

    for key in ("rows_read", "rows_total", "rows_discarded", "duplicate_key_rows_dropped"):
        assert native[key] == pandas_summary[key], key


def test_column_stats_match_the_pandas_path(tmp_project, monkeypatch):
    # "20" against "105": compared as text the order is wrong, and the pandas
    # reader had inferred a number. min/max are taken on the cast column.
    _write("paises", 1, ["20;Vinte", "105;Cento e cinco", "999;Mil"])
    _load("paises", monkeypatch, native=True)
    native = _summary("paises")["column_stats"]

    settings.duckdb_file.unlink()
    _write("paises", 1, ["20;Vinte", "105;Cento e cinco", "999;Mil"])
    _load("paises", monkeypatch, native=False)
    pandas_stats = _summary("paises")["column_stats"]

    assert native == pandas_stats


# --- comportamento que o caminho nativo precisou reproduzir ----------------


def test_utf8_bom_does_not_pollute_the_first_key(tmp_project, monkeypatch):
    _write("paises", 1, ["150;Brasil"], bom=True)
    _load("paises", monkeypatch, native=True)
    assert _rows(settings.duckdb_file, "paises") == [(150, "Brasil")]


def test_empty_field_becomes_null_not_zero(tmp_project, monkeypatch):
    _write("paises", 1, ["150;"])
    _load("paises", monkeypatch, native=True)
    assert _rows(settings.duckdb_file, "paises") == [(150, None)]


def test_latin1_accents_survive(tmp_project, monkeypatch):
    _write("municipios", 1, ["3550308;S\xe3o Paulo"])
    _load("municipios", monkeypatch, native=True)
    assert _rows(settings.duckdb_file, "municipios") == [(3550308, "São Paulo")]


def test_duplicate_key_is_dropped_and_counted(tmp_project, monkeypatch):
    _write("paises", 1, ["150;Brasil", "150;Brasil de Novo"])
    _load("paises", monkeypatch, native=True)
    summary = _summary("paises")
    assert len(_rows(settings.duckdb_file, "paises")) == 1
    assert summary["duplicate_key_rows_dropped"] == 1
    assert summary["rows_read"] == 2
    assert summary["rows_total"] == 1


def test_null_key_still_aborts_the_load(tmp_project, monkeypatch):
    """OR IGNORE covers the key conflict, not NOT NULL. Same as before."""
    _write("paises", 1, ["150;Brasil", ";Sem Codigo"])
    with pytest.raises(Exception):
        _load("paises", monkeypatch, native=True)


def test_summary_declares_the_strategy_that_produced_it(tmp_project, monkeypatch):
    _write("paises", 1, ["150;Brasil"])
    _load("paises", monkeypatch, native=True)
    assert _summary("paises")["load_strategy"] == "duckdb_native"

    settings.duckdb_file.unlink()
    _write("paises", 1, ["150;Brasil"])
    _load("paises", monkeypatch, native=False)
    assert _summary("paises")["load_strategy"] == "pandas_chunked"


def test_multiple_volumes_accumulate(tmp_project, monkeypatch):
    _write("paises", 1, ["150;Brasil"])
    _write("paises", 2, ["160;Portugal"])
    _load("paises", monkeypatch, native=True)
    summary = _summary("paises")
    assert summary["volumes_read"] == 2
    assert summary["rows_total"] == 2


# --- o SQL do leitor é montado como texto, não como relação ----------------


def test_csv_source_is_text_and_takes_the_path_as_a_parameter(tmp_path):
    """A relation over read_csv would materialise the file when composed.

    ``sql_query()`` renders the rows into the string, so anything that builds a
    statement from a relation drags the volume through Python. The source is
    text plus parameters, and nothing is evaluated until execute().
    """
    p = tmp_path / "p.csv"
    p.write_bytes(b"\xef\xbb\xbf150;Brasil\n")
    sql, params = db.csv_source_sql(p, columns=["codigo", "nome"], cast_types={"codigo": "SMALLINT"})
    assert isinstance(sql, str)
    assert sql.startswith("SELECT ")
    assert "read_csv(" in sql
    # the path is a parameter, never interpolated
    assert str(p) not in sql
    assert params[0] == str(p)


def test_encoding_spellings_are_normalised():
    assert db.normalize_encoding("latin1") == "latin-1"
    assert db.normalize_encoding("LATIN-1") == "latin-1"
    assert db.normalize_encoding("utf8") == "utf-8"


def test_bom_literal_is_the_latin1_reading_not_ufeff():
    """EF BB BF decoded as latin-1 is three characters, not one."""
    assert db.BOM_LATIN1 == "ï»¿"
    assert db.BOM_LATIN1 != "\ufeff"