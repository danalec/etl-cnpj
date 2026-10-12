"""Quality gates, quarantine and the chunk writer, against a real DuckDB.

The Receita volumes are headerless: the loader passes ``header=None`` and
supplies the column names, so these fixtures must not include a header row.
That matters more than it looks -- a stray header becomes a data row, and now
that the primary key exists from CREATE TABLE, two volumes that both carry a
header collide on it.
"""

import json

import pandas as pd
import pytest

import src.database_loader as dl
from src import db
from src.settings import settings


def _make_csv(config_name: str, volume: int, text: str, encoding: str = "latin1"):
    d = settings.extracted_dir / config_name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{config_name}{volume}.csv").write_text(text, encoding=encoding)


def _seed_domains(con):
    """Populate only the lookup tables so foreign keys resolve.

    ``empresas`` is left empty: it is the table under test, and seeding it would
    collide with the CSV rows on the primary key.
    """
    con.execute("INSERT INTO paises VALUES (150, 'Brasil')")
    con.execute("INSERT INTO municipios VALUES (1234, 'Cidade')")
    con.execute("INSERT INTO cnaes VALUES (1234567, 'Atividade')")
    con.execute("INSERT INTO naturezas_juridicas VALUES (9999, 'Natureza')")
    con.execute("INSERT INTO qualificacoes_socios VALUES (99, 'Qualificacao')")


def _quarantine_records():
    out = []
    for path in sorted(dl._jsonl_dir("quarantine").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _summary(config_name: str = "empresas"):
    path = dl._jsonl_dir("telemetry") / f"{config_name}_summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_encoding_probe(monkeypatch):
    monkeypatch.setattr(dl, "_detect_encoding", lambda p, default: "latin1")


# --- quality gates -----------------------------------------------------------


def test_quality_gate_discards_the_chunk_and_keeps_the_rows(
    tmp_project, duckdb_con, monkeypatch
):
    """A gated chunk must not reach the table, but its rows must survive.

    Only an aggregate record used to be written, which made the discarded data
    unrecoverable and left the summary unable to account for the gap.
    """
    _make_csv("empresas", 1, "00000001;A\n00000002;B\n")
    _seed_domains(duckdb_con)

    settings.enable_quality_gates = True
    settings.gate_min_rows = 1
    settings.gate_max_changed_ratio = 0.1
    settings.gate_max_null_delta_ratio = 0.1

    monkeypatch.setattr(
        dl,
        "schema_validate",
        lambda n, c: (
            c,
            {"changed_counts": {"razao_social": int(len(c))}, "null_deltas": {}},
            {},
        ),
    )
    dl.clear_domain_cache()
    dl.process_and_load_file(duckdb_con, "empresas")

    assert db.count_rows(duckdb_con, "empresas") == 0, "a gated chunk must not land"

    rows = [r for r in _quarantine_records() if "row" in r]
    assert len(rows) == 2, f"expected both discarded rows, got {len(rows)}"
    assert all(r["reason"] == "quality_gate" for r in rows)

    summary = _summary()
    assert summary["quality_gate_chunks"] == 1
    assert summary["quality_gate_rows_discarded"] == 2
    assert summary["rows_total"] == 0


def test_chunk_below_gate_min_rows_is_not_gated(
    tmp_project, duckdb_con, monkeypatch
):
    _make_csv("empresas", 1, "00000009;X\n00000010;Y\n")
    _seed_domains(duckdb_con)

    settings.enable_quality_gates = True
    settings.gate_min_rows = 100  # above the row count
    settings.gate_max_changed_ratio = 0.0
    settings.gate_max_null_delta_ratio = 0.0

    monkeypatch.setattr(
        dl,
        "schema_validate",
        lambda n, c: (
            c,
            {"changed_counts": {"razao_social": int(len(c))}, "null_deltas": {}},
            {},
        ),
    )
    dl.clear_domain_cache()
    dl.process_and_load_file(duckdb_con, "empresas")

    assert db.count_rows(duckdb_con, "empresas") == 2
    assert _summary()["quality_gate_chunks"] == 0


def test_critical_field_null_is_recorded_and_row_is_skipped(duckdb_con, tmp_project):
    """A row without its primary key is quarantined and not loaded.

    The constraint is part of CREATE TABLE, so the row cannot be stored at all.
    """
    _make_csv("empresas", 1, ";SemCodigo\n00000020;Ok\n")
    _seed_domains(duckdb_con)
    settings.gate_min_rows = 1000
    dl.schema_validate = lambda n, c: (c, {}, {})
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")

    assert "critical_fields_null" in {r.get("reason") for r in _quarantine_records()}
    rows = duckdb_con.execute("SELECT cnpj_basico FROM empresas").fetchall()
    assert rows == [("00000020",)]
    assert _summary()["critical_key_null_rows_skipped"] == 1


def test_orphan_fk_is_neutralised_before_the_engine_sees_it(duckdb_con, tmp_project):
    """The engine enforces the FK, so the loader has to null the reference first."""
    _make_csv("estabelecimentos", 1, "00000001;0001;00;;;;2;;;;;999;;;;1234567;;;;;;;SP;1234;;;;;;;;;\n")
    _seed_domains(duckdb_con)
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000001','Base')"
    )
    settings.gate_min_rows = 1000
    settings.strict_fk_validation = False
    dl.schema_validate = lambda n, c: (c, {}, {})
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "estabelecimentos")
    rows = duckdb_con.execute(
        "SELECT cnpj_basico, pais_codigo FROM estabelecimentos"
    ).fetchall()
    assert rows == [("00000001", None)]


# --- multi-volume ------------------------------------------------------------


def test_every_volume_is_loaded(duckdb_con, tmp_project):
    """The consolidation step is gone: volumes are read in place."""
    _make_csv("empresas", 1, "00000001;A\n")
    _make_csv("empresas", 2, "00000002;B\n")
    _make_csv("empresas", 3, "00000003;C\n")
    dl.schema_validate = lambda n, c: (c, {}, {})
    settings.gate_min_rows = 1000
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")
    assert db.count_rows(duckdb_con, "empresas") == 3


def test_missing_directory_is_skipped(duckdb_con, tmp_project):
    dl.process_and_load_file(duckdb_con, "nao_existe_config")
    assert db.count_rows(duckdb_con, "empresas") == 0


# --- chunk writer ------------------------------------------------------------


def test_fast_load_chunk_writes_rows(duckdb_con):
    frame = pd.DataFrame(
        {
            "cnpj_basico": pd.Series(["00000001", "00000002"], dtype="string"),
            "razao_social": pd.Series(["A", "B"], dtype="string"),
        }
    )
    dl.fast_load_chunk(duckdb_con, frame, "empresas")
    assert db.count_rows(duckdb_con, "empresas") == 2


def test_fast_load_chunk_rolls_back_on_conflict(duckdb_con):
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000001','A')"
    )
    frame = pd.DataFrame(
        {
            "cnpj_basico": pd.Series(["00000001"], dtype="string"),
            "razao_social": pd.Series(["Duplicado"], dtype="string"),
        }
    )
    with pytest.raises(Exception):
        dl.fast_load_chunk(duckdb_con, frame, "empresas")
    assert duckdb_con.execute(
        "SELECT razao_social FROM empresas WHERE cnpj_basico='00000001'"
    ).fetchone()[0] == "A"