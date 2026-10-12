"""End-to-end load against a real DuckDB file.

There is no service to start and no gate to set: DuckDB is embedded, so these
run by default. What they cover is where the engine is strictest: foreign keys
are declared in CREATE TABLE, so the loader has to neutralise invalid
references and backfill missing parents *before* the child load, and a row
without its primary key has to be dropped rather than quarantined only.
"""

import json

import pytest

from src import backfill, db
from src.database_loader import ETL_CONFIG
from src.settings import settings


def _write(config_key: str, volume: int, rows: list[str], encoding: str = "latin1"):
    d = settings.extracted_dir / config_key
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{config_key}{volume}.csv").write_text("\n".join(rows), encoding=encoding)


def _row(config_key: str, values: list[str]) -> str:
    cols = ETL_CONFIG[config_key]["column_names"]
    assert len(values) <= len(cols), (config_key, len(values), len(cols))
    return ";".join(values + [""] * (len(cols) - len(values)))


@pytest.fixture
def sample_dataset(tmp_project, monkeypatch):
    """A small but complete dataset, including one orphan reference."""
    import src.database_loader as dl

    monkeypatch.setattr(dl, "_detect_encoding", lambda p, default: "latin1")
    settings.strict_fk_validation = False
    settings.enable_quality_gates = False

    # paises has no code 999, so the estabelecimento below references an
    # orphan and the backfill has to create it.
    _write("paises", 1, ["150;Brasil"])
    _write("municipios", 1, ["1234;Cidade"])
    _write("qualificacoes", 1, ["99;Qualificacao"])
    _write("naturezas", 1, ["9999;Natureza"])
    _write("cnaes", 1, ["1234567;Atividade"])

    _write(
        "empresas",
        1,
        [_row("empresas", ["00000001", "Empresa Teste", "9999", "99", "1000,00", "1"])],
    )

    estab = [
        "00000001", "0001", "00", "1", "Fantasia LTDA", "2", "20240101", "0", "",
        "999", "20240102", "1234567", "2345678,3456789", "Rua", "Exemplo", "123",
        "", "Centro", "12345678", "SP", "1234", "11", "123456789", "", "", "",
        "", "contato@example.com", "", "20240103",
    ]
    _write("estabelecimentos", 1, [_row("estabelecimentos", estab)])
    second = list(estab)
    second[1:3] = ["0002", "00"]
    second[4] = "Segunda Filial"
    _write("estabelecimentos", 2, [_row("estabelecimentos", second)])

    # A real month ships every table. run_loader refuses to drop a populated
    # database when a planned input is missing.
    _write(
        "simples",
        1,
        [_row("simples", ["00000001", "S", "20240101", "", "M", "20240101", ""])],
    )
    _write(
        "socios",
        1,
        [_row("socios", ["00000001", "1", "Socio Unico", "", "99", "20240101", "150"])],
    )

    return tmp_project


def test_loader_runs_end_to_end(sample_dataset):
    import src.database_loader as dl

    dl.run_loader()
    dl.run_constraints()

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        assert db.count_rows(con, "empresas") == 1
        assert db.count_rows(con, "estabelecimentos") == 2, "both volumes must load"
        assert db.count_rows(con, "paises") == 2, "one real row plus the backfill"
        assert len(db.constraint_names(con)) >= 11
        assert len(db.index_names(con)) >= 10
    finally:
        con.close()


def test_backfill_creates_the_missing_parent(sample_dataset):
    """Without this the orphan reference would abort the child insert."""
    import src.database_loader as dl

    dl.run_loader()

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        row = con.execute("SELECT nome FROM paises WHERE codigo = 999").fetchone()
        assert row is not None, "the orphan pais was never backfilled"
        assert row[0] == backfill.MISSING_PARENT
    finally:
        con.close()


def test_establishment_keeps_its_orphan_reference(sample_dataset):
    """The backfilled parent means the original value is preserved, not nulled."""
    import src.database_loader as dl

    dl.run_loader()
    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        rows = con.execute(
            "SELECT cnpj_ordem, pais_codigo FROM estabelecimentos ORDER BY 1"
        ).fetchall()
        assert rows == [("0001", 999), ("0002", 999)]
    finally:
        con.close()


def test_row_without_primary_key_is_skipped(sample_dataset):
    import src.database_loader as dl

    _write("empresas", 2, [_row("empresas", ["", "Sem codigo"])])
    dl.run_loader()

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        rows = con.execute("SELECT cnpj_basico FROM empresas").fetchall()
        assert rows == [("00000001",)]
        records = [
            json.loads(line)
            for path in settings.quarantine_dir.rglob("empresas*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(r.get("reason") == "critical_fields_null" for r in records)
    finally:
        con.close()


def test_reload_is_idempotent(sample_dataset):
    """Running the loader twice must not duplicate rows or trip a constraint."""
    import src.database_loader as dl

    dl.run_loader()
    dl.run_loader()

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        assert db.count_rows(con, "empresas") == 1
        assert db.count_rows(con, "estabelecimentos") == 2
    finally:
        con.close()


def test_backfill_is_skipped_when_disabled(sample_dataset):
    import src.database_loader as dl

    settings.enable_constraints_backfill = False
    dl.run_loader()

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        assert db.count_rows(con, "paises") == 1, "no synthetic parent should exist"
    finally:
        con.close()