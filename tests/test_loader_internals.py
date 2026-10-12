import duckdb
import pandas as pd
import pytest

from src import db


# --- Helpers -----------------------------------------------------------------


def _critical_fields_map():
    import src.database_loader as dl

    assert dl._critical_fields("empresas") == ["cnpj_basico"]
    assert dl._critical_fields("estabelecimentos") == [
        "cnpj_basico",
        "cnpj_ordem",
        "cnpj_dv",
    ]


def _jsonl_dir(kind):
    import src.database_loader as dl

    return dl._jsonl_dir(kind)


def _jsonl_files():
    """Read back everything _write_jsonl recorded."""

    out = []
    for kind in ("telemetry", "quarantine"):
        for path in sorted(_jsonl_dir(kind).glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    import json

                    out.append((kind, json.loads(line)))
    return out


# --- db seam -----------------------------------------------------------------


def test_quote_ident_rejects_escapes():
    assert db.quote_ident("empresas") == '"empresas"'
    with pytest.raises(ValueError):
        db.quote_ident('t" ; DROP TABLE x; --')


def test_transaction_rolls_back_the_whole_unit(duckdb_con):
    with pytest.raises(RuntimeError):
        with db.transaction(duckdb_con):
            duckdb_con.execute("CREATE TABLE probe (a INT)")
            duckdb_con.execute("INSERT INTO probe VALUES (1)")
            raise RuntimeError("boom")
    assert db.table_exists(duckdb_con, "probe") is False


def test_transaction_commits_when_nothing_fails(duckdb_con):
    with db.transaction(duckdb_con):
        duckdb_con.execute("CREATE TABLE probe (a INT)")
        duckdb_con.execute("INSERT INTO probe VALUES (1)")
    assert db.count_rows(duckdb_con, "probe") == 1


def test_apply_script_is_atomic(duckdb_con):
    """A multi-statement script must not leave half of itself applied.

    DuckDB commits statement by statement, so without the explicit transaction a
    failure midway would persist everything before it.
    """
    script = """
        CREATE TABLE half (a INT);
        INSERT INTO half VALUES (1);
        SELECT * FROM does_not_exist;
    """
    with pytest.raises(Exception):
        db.apply_script(duckdb_con, script)
    assert db.table_exists(duckdb_con, "half") is False


def test_append_dataframe_inserts_by_column_name(duckdb_con):
    frame = pd.DataFrame({"cnpj_basico": ["00000001"], "razao_social": ["A"]})
    inserted = db.append_dataframe(duckdb_con, frame, "empresas")
    assert inserted == 1
    assert duckdb_con.execute(
        "SELECT razao_social FROM empresas WHERE cnpj_basico='00000001'"
    ).fetchone()[0] == "A"


def test_append_dataframe_on_empty_frame_is_a_noop(duckdb_con):
    frame = pd.DataFrame({"pais_codigo": pd.Series([], dtype="string"), "razao_social": pd.Series([], dtype="string")})
    assert db.append_dataframe(duckdb_con, frame, "empresas") == 0


# --- domain helpers ----------------------------------------------------------


def test_distinct_values_skips_nulls(duckdb_con):
    # A primary key column cannot be NULL, so the null check is exercised on a
    # nullable one.
    duckdb_con.execute("INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000001','A')")
    duckdb_con.execute("INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000002','A')")
    duckdb_con.execute("INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000003',NULL)")
    assert db.distinct_values(duckdb_con, "empresas", "razao_social") == {"A"}


def test_count_rows(duckdb_con):
    assert db.count_rows(duckdb_con, "empresas") == 0
    duckdb_con.execute("INSERT INTO paises VALUES (1, 'x')")
    assert db.count_rows(duckdb_con, "paises") == 1


def test_table_exists(duckdb_con):
    assert db.table_exists(duckdb_con, "empresas") is True
    assert db.table_exists(duckdb_con, "nao_existe") is False


# --- foreign keys are real ---------------------------------------------------


def test_foreign_key_rejects_orphan(duckdb_con):
    """DuckDB enforces the constraint, which is what the loader relies on."""
    duckdb_con.execute("INSERT INTO naturezas_juridicas VALUES (9999, 'N')")
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, natureza_juridica_codigo) "
        "VALUES ('00000001', 9999)"
    )
    with pytest.raises(duckdb.ConstraintException):
        duckdb_con.execute(
            "INSERT INTO empresas (cnpj_basico, natureza_juridica_codigo) "
            "VALUES ('00000002', 1234)"
        )


def test_foreign_key_allows_null(duckdb_con):
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, natureza_juridica_codigo) "
        "VALUES ('00000001', NULL)"
    )
    assert db.count_rows(duckdb_con, "empresas") == 1


def test_primary_key_rejects_duplicate(duckdb_con):
    duckdb_con.execute("INSERT INTO paises VALUES (150, 'Brasil')")
    with pytest.raises(duckdb.ConstraintException):
        duckdb_con.execute("INSERT INTO paises VALUES (150, 'Brasil de novo')")


def test_schema_declares_the_expected_constraints(duckdb_con):
    fks = {
        r[0]
        for r in duckdb_con.execute(
            "SELECT constraint_name FROM duckdb_constraints() "
            "WHERE constraint_type='FOREIGN KEY'"
        ).fetchall()
    }
    assert len(fks) == 11, f"unexpected foreign key count: {sorted(fks)}"
    assert len(db.index_names(duckdb_con)) >= 10


# --- cleaners ----------------------------------------------------------------


def test_critical_fields_map():
    _critical_fields_map()


def test_sanitize_dates_and_cleaners(duckdb_con):
    import src.database_loader as dl

    frame = pd.DataFrame(
        {
            "capital_social": ["1000,00"],
            "data_situacao_cadastral": ["20240101"],
            "uf": ["sp"],
        }
    )
    out = dl.sanitize_dates(frame, ["data_situacao_cadastral"])
    assert "data_situacao_cadastral" in out.columns


def test_quarantine_files_are_written_as_jsonl(tmp_project, monkeypatch):
    """The quarantine output has to be readable back; it is the audit trail."""
    import src.database_loader as dl

    dl._write_jsonl("quarantine", "empresas", {"reason": "teste", "row": {"a": 1}})
    found = _jsonl_files()
    assert ("quarantine", {"reason": "teste", "row": {"a": 1}}) in found


def test_quarantine_rotates_and_keeps_both_files(tmp_project):
    import src.database_loader as dl

    dl._write_jsonl("quarantine", "empresas", {"n": 1})
    dl._write_jsonl("quarantine", "empresas", {"n": 2})
    records = [r for _, r in _jsonl_files() if r.get("n")]
    assert {r["n"] for r in records} == {1, 2}


def test_detect_encoding_falls_back_to_default(tmp_path):
    import src.database_loader as dl

    assert dl._detect_encoding(tmp_path / "ausente.csv", "latin1") == "latin1"


def test_domain_cache_is_cleared(duckdb_con):
    import src.database_loader as dl

    dl._get_domain_set(duckdb_con, "paises", "codigo")
    assert dl.DOMAIN_CACHE
    dl.clear_domain_cache()
    assert dl.DOMAIN_CACHE == {}