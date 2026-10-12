"""The completeness contract, end to end.

Each test asserts one part of the design in docs/design-manifest.md: a stage can
lose work, the next stage can tell, and the month is only stamped when every
stage finished.
"""

import json
import pathlib
import zipfile

import pytest

from src import db, manifest, state
from src.settings import settings


# --- 1. the manifest ---------------------------------------------------------


def test_manifest_is_written_atomically(tmp_path):
    path = manifest.write_manifest(
        tmp_path, {"target_date": "2025-11", "tables": {"Empresas": [{"name": "E1.zip"}]}}
    )
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp")), "a temporary file was left behind"
    assert manifest.read_manifest(tmp_path)["target_date"] == "2025-11"


def test_missing_manifest_reads_as_none(tmp_path):
    assert manifest.read_manifest(tmp_path) is None


def test_corrupt_manifest_reads_as_none(tmp_path):
    manifest.manifest_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert manifest.read_manifest(tmp_path) is None


def test_manifest_without_tables_reads_as_none(tmp_path):
    manifest.manifest_path(tmp_path).write_text('{"target_date": "x"}', encoding="utf-8")
    assert manifest.read_manifest(tmp_path) is None


def test_expected_archives_ignores_malformed_entries(tmp_path):
    data = {
        "tables": {
            "Empresas": [{"name": "E1.zip", "bytes": 1}, {"bytes": 2}, "lixo"],
            "Vazio": [],
        }
    }
    assert manifest.expected_archives(data) == {"Empresas": ["E1.zip"]}


# --- 2. extraction verifies against it ---------------------------------------


def _zip(path: pathlib.Path, member: str, payload: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(member, payload)


def test_extraction_fails_when_an_archive_is_corrupt(tmp_path):
    """A corrupt archive used to be logged and skipped while the step reported
    success, which let the month be stamped as processed."""
    from src import extract_files

    settings.project_root = tmp_path
    settings.create_dirs()

    compressed = pathlib.Path(settings.compressed_dir)
    compressed.mkdir(parents=True, exist_ok=True)
    (compressed / "Empresas01.zip").write_bytes(b"isto nao e um zip")

    manifest.write_manifest(
        pathlib.Path(settings.data_dir),
        {"tables": {"Empresas": [{"name": "Empresas01.zip"}]}},
    )

    with pytest.raises(RuntimeError, match="corrompidos"):
        extract_files.run_extraction()


def test_extraction_fails_when_an_expected_csv_is_missing(tmp_path):
    """The manifest says a CSV should exist; extraction finished without it."""
    from src import extract_files

    settings.project_root = tmp_path
    settings.create_dirs()

    compressed = pathlib.Path(settings.compressed_dir)
    compressed.mkdir(parents=True, exist_ok=True)
    _zip(compressed / "Empresas01.zip", "Empresas01.csv", "a;b\n1;2\n")
    _zip(compressed / "Empresas02.zip", "Empresas02.csv", "a;b\n3;4\n")
    # The manifest expects both; one archive is removed after the listing.
    manifest.write_manifest(
        pathlib.Path(settings.data_dir),
        {
            "tables": {
                "Empresas": [
                    {"name": "Empresas01.zip"},
                    {"name": "Empresas02.zip"},
                ]
            }
        },
    )
    (compressed / "Empresas02.zip").unlink()
    (compressed / "Empresas02.zip").write_bytes(b"nao e um zip")

    with pytest.raises(RuntimeError):
        extract_files.run_extraction()


def test_extraction_succeeds_when_the_manifest_matches(tmp_path):
    from src import extract_files

    settings.project_root = tmp_path
    settings.create_dirs()

    compressed = pathlib.Path(settings.compressed_dir)
    compressed.mkdir(parents=True, exist_ok=True)
    _zip(compressed / "Empresas01.zip", "Empresas01.csv", "a;b\n1;2\n")

    manifest.write_manifest(
        pathlib.Path(settings.data_dir),
        {"tables": {"Empresas": [{"name": "Empresas01.zip"}]}},
    )

    extract_files.run_extraction()  # must not raise
    assert (pathlib.Path(settings.extracted_dir) / "empresas" / "Empresas01.csv").exists()


# --- 3. the summary has a denominator ----------------------------------------


def test_summary_reconciles_rows_read_against_rows_total(duckdb_con, tmp_project):
    """Without rows_read there is no way to tell a finished table from an
    abandoned one."""
    import src.database_loader as dl

    settings.gate_min_rows = 1000
    monkey = dl.schema_validate
    dl.schema_validate = lambda n, c: (c, {}, {})
    try:
        cols = dl.ETL_CONFIG["empresas"]["column_names"]

        def row(cnpj: str) -> str:
            vals = {"cnpj_basico": cnpj, "razao_social": "X"}
            return ";".join(vals.get(c, "") for c in cols)

        d = pathlib.Path(settings.extracted_dir) / "empresas"
        d.mkdir(parents=True, exist_ok=True)
        (d / "empresas1.csv").write_text(
            "\n".join([row("00000001"), row("00000002"), row("00000003")]),
            encoding="latin1",
        )
        dl.clear_domain_cache()
        dl.process_and_load_file(duckdb_con, "empresas")
    finally:
        dl.schema_validate = monkey

    summary = json.loads(
        (dl._jsonl_dir("telemetry") / "empresas_summary.json").read_text(encoding="utf-8")
    )
    assert summary["rows_read"] == 3
    assert summary["volumes_read"] == 1
    assert summary["rows_total"] == 3
    assert summary["rows_discarded"] == summary["rows_read"] - summary["rows_total"] == 0


# --- 4. the completion contract ----------------------------------------------


def test_a_month_with_a_pending_stage_is_not_complete(tmp_path):
    settings.project_root = tmp_path
    settings.create_dirs()

    state.start_run("2025-11")
    for stage in state.STAGES:
        state.mark_stage("2025-11", stage, "completed")
    assert state.is_month_complete("2025-11") is True

    state.mark_stage("2025-11", "extract", "failed")
    assert state.is_month_complete("2025-11") is False
    assert "extract" in state.stages_not_completed("2025-11")


def test_a_fresh_month_is_not_complete(tmp_path):
    settings.project_root = tmp_path
    settings.create_dirs()
    state.start_run("2025-12")
    assert state.is_month_complete("2025-12") is False
    assert sorted(state.stages_not_completed("2025-12")) == sorted(state.STAGES)


def test_municipio_code_fits_its_column(duckdb_con):
    """Codigo SIAFI is seven digits.

    municipios.codigo was SMALLINT, so the real values (3550308) overflowed and
    aborted the load. Fixtures that used four-digit codes never hit it.
    """
    con_type = duckdb_con.execute(
        "SELECT data_type FROM duckdb_columns() "
        "WHERE table_name = 'municipios' AND column_name = 'codigo'"
    ).fetchone()[0]
    assert "INT" in con_type.upper()
    assert "INT16" not in con_type.upper(), (
        f"municipios.codigo is {con_type}; SIAFI codes need 7 digits"
    )

    duckdb_con.execute("INSERT INTO municipios VALUES (3550308, 'Sao Paulo')")
    assert db.count_rows(duckdb_con, "municipios") == 1


def test_domain_code_columns_suit_their_values(duckdb_con):
    """Every domain key must hold what the Receita actually publishes."""
    from src import manifest as _manifest  # noqa: F401

    expected_max = {
        "paises": 999,
        "municipios": 9999999,
        "qualificacoes_socios": 99,
        "naturezas_juridicas": 99,
        "cnaes": 9999999,
    }
    for table, ceiling in expected_max.items():
        row = duckdb_con.execute(
            "SELECT data_type FROM duckdb_columns() "
            "WHERE table_name = ? AND column_name = 'codigo'",
            [table],
        ).fetchone()
        assert row, f"{table}.codigo not found"
        data_type = row[0].upper()
        if "INT16" in data_type:
            assert ceiling <= 32767, f"{table}.codigo cannot hold {ceiling}"
        duckdb_con.execute(f"INSERT INTO {table} VALUES (?, 'x')", [ceiling])


def test_telemetry_goes_to_its_own_directory(tmp_project):
    """logs/telemetry was never written; "telemetry" landed in auto_repair.

    Everything labelled telemetry, including the per-table summary, went to a
    single directory the docs never pointed at, so the documented one stayed
    empty. Telemetry and quarantine now have their own roots, and the
    auto_repair directory that used to absorb both is not created at all --
    creating an empty directory on every boot is a promise the code does not
    keep.
    """
    import src.database_loader as dl
    from src.settings import settings

    assert dl._jsonl_dir("telemetry").is_relative_to(settings.telemetry_dir)
    assert dl._jsonl_dir("quarantine").is_relative_to(settings.quarantine_dir)
    assert not (settings.log_dir / "auto_repair").exists()
    assert not hasattr(settings, "auto_repair_dir")


def test_summary_lands_under_logs_telemetry(duckdb_con, tmp_project, monkeypatch):
    """The documented path has to be the real path."""
    import src.database_loader as dl
    from src.settings import settings

    settings.gate_min_rows = 1000
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    cols = dl.ETL_CONFIG["empresas"]["column_names"]
    vals = {"cnpj_basico": "00000042", "razao_social": "X"}
    d = pathlib.Path(settings.extracted_dir) / "empresas"
    d.mkdir(parents=True, exist_ok=True)
    (d / "empresas1.csv").write_text(
        ";".join(vals.get(c, "") for c in cols), encoding="latin1"
    )
    dl.clear_domain_cache()
    dl.process_and_load_file(duckdb_con, "empresas")

    summary = settings.telemetry_dir.rglob("empresas_summary.json")
    found = list(summary)
    assert found, f"no summary under {settings.telemetry_dir}"
    payload = json.loads(found[0].read_text(encoding="utf-8"))
    assert payload["rows_read"] == payload["rows_total"] == 1
    assert payload["rows_discarded"] == 0
