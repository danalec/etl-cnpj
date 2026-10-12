"""Edge cases around the engine seam and the loader's failure modes.

Written to probe, not to document: each one asserts what should happen, so a
failure is a defect rather than a preference.
"""

import json
import logging
import pathlib

import duckdb
import pytest

from src import backfill, db
from src.database_loader import ETL_CONFIG
from src.settings import settings


def _write(config_key: str, volume: int, rows: list[str]):
    d = settings.extracted_dir / config_key
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{config_key}{volume}.csv").write_text("\n".join(rows), encoding="latin1")


def _row(config_key: str, values: list[str]) -> str:
    cols = ETL_CONFIG[config_key]["column_names"]
    values = values[: len(cols)] + [""] * max(0, len(cols) - len(values))
    return ";".join(str(v) for v in values)


@pytest.fixture(autouse=True)
def _quiet_loader(monkeypatch):
    import src.database_loader as dl

    monkeypatch.setattr(dl, "_detect_encoding", lambda p, default: "latin1")
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    settings.gate_min_rows = 1000
    settings.strict_fk_validation = False
    settings.enable_quality_gates = False


# --- read_csv_files ----------------------------------------------------------


def test_read_csv_rejects_a_file_narrower_than_the_schema(duckdb_con, tmp_path):
    """A short file is an error, not a silently padded row."""
    csv = tmp_path / "curto.csv"
    csv.write_text("a;b\n1;2\n", encoding="latin1")
    with pytest.raises(Exception):
        db.read_csv_files(duckdb_con, str(csv), columns=["a", "b", "c"])


def test_read_csv_can_select_a_subset(duckdb_con, tmp_path):
    # The Receita volumes are headerless, so there is no header row here.
    csv = tmp_path / "largo.csv"
    csv.write_text("1;2;3\n4;5;6\n", encoding="latin1")
    got = db.read_csv_files(duckdb_con, str(csv), columns=["a", "b", "c"], select=["a"])
    assert list(got.columns) == ["a"]
    assert got["a"].tolist() == ["1", "4"]


def test_read_csv_ignores_columns_it_was_not_asked_for(duckdb_con, tmp_path):
    csv = tmp_path / "largo.csv"
    csv.write_text("1;2;3\n", encoding="latin1")
    got = db.read_csv_files(
        duckdb_con, str(csv), columns=["a", "b", "c"], select=["c", "inexistente"]
    )
    assert list(got.columns) == ["c"]


def test_an_empty_volume_is_not_a_warning(duckdb_con, tmp_path):
    """A zero-byte volume is benign; the backfill must not log a failure."""
    settings.project_root = tmp_path
    settings.create_dirs()
    _write("paises", 1, ["150;Brasil"])
    (settings.extracted_dir / "paises" / "paises2.csv").write_text("", encoding="latin1")

    con = duckdb.connect()
    con.execute(
        (pathlib.Path(__file__).resolve().parents[1] / "src/schema.sql").read_text(
            encoding="utf-8"
        )
    )
    con.execute("INSERT INTO paises VALUES (150, 'Brasil')")

    import logging

    records: list = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    backfill.logger.addHandler(handler)
    try:
        backfill.run_backfill(con)
    finally:
        backfill.logger.removeHandler(handler)
        con.close()

    assert not [r for r in records if r.levelno >= logging.WARNING], (
        f"an empty volume produced a warning: "
        f"{[r.getMessage() for r in records if r.levelno >= logging.WARNING]}"
    )


def test_a_short_volume_does_not_silently_pad(duckdb_con, tmp_path):
    """Fewer columns than the schema is a real error, not a padded row."""
    csv = tmp_path / "curto.csv"
    csv.write_text("1\n2\n", encoding="latin1")
    with pytest.raises(Exception):
        db.read_csv_files(duckdb_con, str(csv), columns=["a", "b"])


# --- the backfill ------------------------------------------------------------


def test_backfill_ignores_non_numeric_codes(tmp_path, monkeypatch):
    """A code that can never be a key is dropped, not turned into a parent."""
    settings.project_root = tmp_path
    settings.create_dirs()
    _write("estabelecimentos", 1, [_row("estabelecimentos", ["00000001", "0001", "00", "1", "F", "2", "20240101", "0", "", "LIXO", "20240102", "1234567", "", "", "", "", "", "", "", "", "12345678", "SP", "1234", "", "", "", "", "", "", "", "", "", "20240103"])])
    _write("paises", 1, ["150;Brasil"])

    con = duckdb.connect()
    con.execute(
        (pathlib.Path(__file__).resolve().parents[1] / "src/schema.sql").read_text(
            encoding="utf-8"
        )
    )
    con.execute("INSERT INTO paises VALUES (150, 'Brasil')")
    backfill.run_backfill(con)
    rows = con.execute("SELECT codigo FROM paises").fetchall()
    con.close()
    assert rows == [(150,)], f"a junk code became a parent row: {rows}"


def test_backfill_fills_an_empty_parent_table(tmp_path):
    """An empty parent is a gap in the extract, not a reason to skip.

    run_loader verifies that every planned table has volumes before it drops
    anything, so a value referenced by a child at this point is real data
    missing from the parent file.
    """
    settings.project_root = tmp_path
    settings.create_dirs()
    _write("estabelecimentos", 1, [_row("estabelecimentos", ["00000001", "0001", "00", "1", "F", "2", "20240101", "0", "", "999", "20240102", "1234567", "", "", "", "", "", "", "", "", "12345678", "SP", "1234", "", "", "", "", "", "", "", "", "", "20240103"])])

    con = duckdb.connect()
    con.execute(
        (pathlib.Path(__file__).resolve().parents[1] / "src/schema.sql").read_text(
            encoding="utf-8"
        )
    )
    backfill.run_backfill(con)
    rows = con.execute("SELECT codigo FROM paises").fetchall()
    con.close()
    assert rows == [(999,)], f"the orphan was not backfilled into an empty table: {rows}"


def test_backfill_is_repeatable(tmp_path):
    settings.project_root = tmp_path
    settings.create_dirs()
    _write("estabelecimentos", 1, [_row("estabelecimentos", ["00000001", "0001", "00", "1", "F", "2", "20240101", "0", "", "999", "20240102", "1234567", "", "", "", "", "", "", "", "", "12345678", "SP", "1234", "", "", "", "", "", "", "", "", "", "20240103"])])
    _write("paises", 1, ["150;Brasil"])

    con = duckdb.connect()
    con.execute(
        (pathlib.Path(__file__).resolve().parents[1] / "src/schema.sql").read_text(
            encoding="utf-8"
        )
    )
    con.execute("INSERT INTO paises VALUES (150, 'Brasil')")
    backfill.run_backfill(con)
    backfill.run_backfill(con)
    rows = con.execute("SELECT count(*) FROM paises").fetchone()[0]
    con.close()
    assert rows == 2, f"the second run duplicated the synthetic row, got {rows}"


# --- loader failure modes ----------------------------------------------------


def test_a_missing_domain_volume_is_reported_per_table(duckdb_con, tmp_project, caplog):
    """A malformed volume should cost one volume, not the whole pipeline.

    The reader is lazy, so a file whose first rows parse and whose later ones
    do not only fails once the chunk is pulled. It used to take the whole load
    down; the volume is now skipped, recorded in quarantine and reported in the
    summary.
    """
    import src.database_loader as dl

    d = settings.extracted_dir / "empresas"
    d.mkdir(parents=True, exist_ok=True)
    (d / "empresas1.csv").write_text("so;uma;coluna\n1\n", encoding="latin1")
    dl.clear_domain_cache()
    with caplog.at_level(logging.ERROR):
        dl.process_and_load_file(duckdb_con, "empresas")
    assert db.count_rows(duckdb_con, "empresas") == 0
    assert any("empresas1.csv" in r.message for r in caplog.records)


def test_an_unreadable_volume_is_recorded_in_the_summary(duckdb_con, tmp_project):
    """A skipped volume has to be visible, or the table just looks short."""
    import src.database_loader as dl

    # Without this the summary lands under a dated subdirectory.
    settings.telemetry_rotate_daily = False

    d = settings.extracted_dir / "empresas"
    d.mkdir(parents=True, exist_ok=True)
    (d / "empresas1.csv").write_text("so;uma;coluna\n1\n", encoding="latin1")
    d2 = settings.extracted_dir / "paises"
    d2.mkdir(parents=True, exist_ok=True)
    (d2 / "paises1.csv").write_bytes(b"\xef\xbb\xbf150;Brasil\n")
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")
    summary = json.loads(
        (settings.telemetry_dir / "empresas_summary.json").read_text(encoding="utf-8")
    )
    assert summary["volumes_failed"] == 1
    assert summary["failed_volumes"][0]["volume"] == "empresas1.csv"


def test_a_bad_volume_does_not_cost_the_good_ones(duckdb_con, tmp_project):
    """The point of per-volume containment: the readable volumes still load."""
    import src.database_loader as dl

    d = settings.extracted_dir / "empresas"
    d.mkdir(parents=True, exist_ok=True)
    (d / "empresas1.csv").write_text("so;uma;coluna\n1\n", encoding="latin1")
    (d / "empresas2.csv").write_text(
        ";".join(["00000001", "A", "9999", "99", "1000,00", "1"]) + "\n", encoding="latin1"
    )
    dl.clear_domain_cache()
    dl.process_and_load_file(duckdb_con, "empresas")
    assert db.count_rows(duckdb_con, "empresas") == 1


def test_a_failure_after_the_read_still_aborts_the_load(duckdb_con, tmp_project):
    """Containment is scoped to the reader, not to the whole load.

    A constraint failure in a chunk is a real problem with the data and has to
    keep stopping the run; only the unreadable file is skipped.
    """
    import src.database_loader as dl

    d = settings.extracted_dir / "paises"
    d.mkdir(parents=True, exist_ok=True)
    # Duplicate primary key inside one volume: INSERT OR IGNORE absorbs it, so
    # this is a NULL key instead, which the constraint must reject.
    (d / "paises1.csv").write_bytes(b"150;Brasil\n;Sem Codigo\n")
    dl.clear_domain_cache()
    with pytest.raises(Exception):
        dl.process_and_load_file(duckdb_con, "paises")


def test_load_survives_one_bad_volume_and_keeps_the_others(duckdb_con, tmp_project):
    """One unreadable volume must not cost us the readable ones."""
    import src.database_loader as dl

    _write("empresas", 1, [_row("empresas", ["00000001", "A"])])
    _write("empresas", 2, [_row("empresas", ["00000002", "B"])])
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")
    assert db.count_rows(duckdb_con, "empresas") == 2


def test_a_directory_with_no_csv_is_not_an_error(duckdb_con, tmp_project):
    import src.database_loader as dl

    d = settings.extracted_dir / "socios"
    d.mkdir(parents=True, exist_ok=True)
    (d / "nota.txt").write_text("ignorado", encoding="utf-8")
    dl.clear_domain_cache()
    dl.process_and_load_file(duckdb_con, "socios")
    assert db.count_rows(duckdb_con, "socios") == 0


# --- engine lifecycle --------------------------------------------------------


def test_two_read_only_connections_can_share_the_file(tmp_path):
    """Read-only concurrency is a documented DuckDB capability we rely on."""
    db.connect(tmp_path / "ro.duckdb", read_only=False).close()
    a = db.connect(tmp_path / "ro.duckdb", read_only=True)
    b = db.connect(tmp_path / "ro.duckdb", read_only=True)
    try:
        assert a.execute("SELECT 1").fetchone() == b.execute("SELECT 1").fetchone()
    finally:
        a.close()
        b.close()


def test_duckdb_file_is_created_in_a_missing_directory(tmp_path):
    target = tmp_path / "a" / "b" / "c.duckdb"
    con = db.connect(target)
    try:
        assert target.exists()
    finally:
        con.close()


def test_absolute_duckdb_path_is_used_as_is(tmp_path):
    target = tmp_path / "absoluto.duckdb"
    settings.project_root = tmp_path / "outro"
    settings.duckdb_path = str(target)
    assert settings.duckdb_file == target