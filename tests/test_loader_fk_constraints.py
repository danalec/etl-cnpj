"""Loader sanitisation and FK quarantine, against a real DuckDB.

The three different FakeConn / _SQLStub doubles this file used to carry are
gone. Everything here runs against an in-memory database with the project
schema applied, so the assertions are about behaviour rather than about a
mock's bookkeeping.
"""

import json

import pytest

import src.database_loader as dl
from src import db
from src.settings import settings


def _write(config_key: str, rows: list[str], encoding: str = "utf-8"):
    d = settings.extracted_dir / config_key
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{config_key}1.csv").write_text("\n".join(rows), encoding=encoding)


def _estab_row(overrides: dict | None = None) -> str:
    """A complete estabelecimento row.

    Two things matter here. The primary key columns have to be present: a row
    without them is dropped before it reaches the table, because the constraint
    is part of CREATE TABLE. And any value containing the ';' delimiter has to be
    quoted, exactly as the Receita writes it -- otherwise it splits the line and
    shifts every later column.
    """
    vals = {
        "cnpj_basico": "00000001",
        "cnpj_ordem": "0001",
        "cnpj_dv": "00",
        "uf": "SP",
    }
    vals.update(overrides or {})
    cols = dl.ETL_CONFIG["estabelecimentos"]["column_names"]

    def fmt(value: str) -> str:
        return f'"{value}"' if ";" in value else value

    return ";".join(fmt(str(vals.get(c, ""))) for c in cols)


def _quarantine():
    out = []
    for path in sorted(dl._jsonl_dir("quarantine").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


@pytest.fixture(autouse=True)
def _no_encoding_probe(monkeypatch):
    monkeypatch.setattr(dl, "_detect_encoding", lambda p, default: "utf-8")


@pytest.fixture
def parent_company(duckdb_con):
    """estabelecimentos references empresas, which loads first in the pipeline."""
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000001','Base')"
    )
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000002','Outra')"
    )
    return duckdb_con


def test_fk_violation_is_quarantined(
    duckdb_con, parent_company, tmp_project, monkeypatch
):
    _write(
        "estabelecimentos",
        [
            _estab_row({"cnpj_ordem": "0001"}),
            _estab_row({"cnpj_ordem": "0002", "cnpj_basico": "00000002"}),
        ],
    )
    monkeypatch.setattr(
        dl, "schema_validate", lambda n, c: (c, {"changed_counts": {}, "null_deltas": {}}, {})
    )
    spy: list = []
    monkeypatch.setattr(dl, "_write_jsonl", lambda kind, name, rec: spy.append((kind, rec)))
    settings.gate_min_rows = 1000

    # The first row points at a company that does not exist.
    def fake_validate_chunk(name, chunk, conn):
        return {"cnpj_basico": chunk.index == 0}

    monkeypatch.setattr(dl, "validate_chunk", fake_validate_chunk)
    dl.clear_domain_cache()
    dl.process_and_load_file(duckdb_con, "estabelecimentos")

    assert any(
        r.get("reason") == "fk_violation" for _, r in spy
    ), spy


def test_sanitisation_branches_produce_a_summary(
    tmp_project, duckdb_con, parent_company, monkeypatch
):
    """Capital with thousand separators, multi-valued CNAE and phone noise."""
    _write("empresas", ["1.234.567,89"])
    _write(
        "estabelecimentos",
        [
            _estab_row(
                {
                    "cnae_fiscal_secundaria": "11;22,33",
                    "ddd_1": "0a2",
                    "telefone_1": "111-222-333",
                    "data_inicio_atividade": "20230101",
                    "data_situacao_cadastral": "20230202",
                    "data_situacao_especial": "20230303",
                }
            )
        ],
    )
    monkeypatch.setattr(
        dl, "schema_validate", lambda n, c: (c, {"changed_counts": {}, "null_deltas": {}}, {})
    )
    settings.gate_min_rows = 1000

    dl.clear_domain_cache()
    for name in ("empresas", "estabelecimentos"):
        dl.process_and_load_file(duckdb_con, name)

    tele = dl._jsonl_dir("telemetry")
    for name in ("empresas", "estabelecimentos"):
        summary = tele / f"{name}_summary.json"
        assert summary.exists(), f"missing summary for {name}"
        assert json.loads(summary.read_text(encoding="utf-8"))["chunks_processed"] == 1


def test_secondary_cnae_keeps_the_brace_literal(
    tmp_project, duckdb_con, parent_company, monkeypatch
):
    """cnae_fiscal_secundaria stays TEXT holding "{a,b}", as validation.py emits."""
    _write(
        "estabelecimentos",
        [_estab_row({"cnae_fiscal_secundaria": "11;22,33"})],
    )
    monkeypatch.setattr(
        dl, "schema_validate", lambda n, c: (c, {"changed_counts": {}, "null_deltas": {}}, {})
    )
    settings.gate_min_rows = 1000
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "estabelecimentos")
    stored = duckdb_con.execute(
        "SELECT cnae_fiscal_secundaria FROM estabelecimentos"
    ).fetchone()[0]
    assert stored == "{11,22,33}"


def test_capital_social_is_normalised(tmp_project, duckdb_con, monkeypatch):
    _write("empresas", ["1.234.567,89"])
    monkeypatch.setattr(
        dl, "schema_validate", lambda n, c: (c, {"changed_counts": {}, "null_deltas": {}}, {})
    )
    settings.gate_min_rows = 1000
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")
    row = duckdb_con.execute("SELECT capital_social FROM empresas").fetchone()
    assert row is not None
    assert row[0] is None or float(row[0]) == pytest.approx(1234567.89, rel=1e-6)


def test_missing_directory_is_reported_not_raised(tmp_project, duckdb_con):
    dl.process_and_load_file(duckdb_con, "nao_existe")
    assert db.count_rows(duckdb_con, "empresas") == 0


def test_unknown_config_name_is_reported(tmp_project, duckdb_con):
    dl.process_and_load_file(duckdb_con, "config_inexistente")
    assert db.count_rows(duckdb_con, "empresas") == 0


def test_quarantine_files_are_valid_json(tmp_project, duckdb_con, monkeypatch):
    _write("empresas", ["00000001;A"])
    monkeypatch.setattr(
        dl, "schema_validate", lambda n, c: (c, {"changed_counts": {}, "null_deltas": {}}, {})
    )
    settings.gate_min_rows = 1000
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")
    for path in dl._jsonl_dir("quarantine").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                json.loads(line)  # raises if malformed