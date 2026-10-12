"""Contracts the pipeline claims to honour.

Each test here asserts a promise the pipeline makes, so a failure is a defect
rather than a style disagreement. They are deliberately written against
behaviour, not implementation.
"""

import json
import pathlib

import duckdb
import numpy as np
import pandas as pd
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
    values = values[: len(cols)] + [""] * max(0, len(cols) - len(values))
    return ";".join(str(v) for v in values)


@pytest.fixture(autouse=True)
def _no_encoding_probe(monkeypatch):
    import src.database_loader as dl

    monkeypatch.setattr(dl, "_detect_encoding", lambda p, default: "latin1")


# --- 1. configuration must actually be applied --------------------------------


def test_duckdb_threads_setting_reaches_the_engine(tmp_path):
    """DUCKDB_THREADS is documented; it has to actually be set."""
    settings.duckdb_threads = 3
    con = db.connect(tmp_path / "threads.duckdb")
    try:
        assert con.execute("SELECT current_setting('threads')").fetchone()[0] == 3
    finally:
        con.close()


def test_duckdb_memory_limit_setting_reaches_the_engine(tmp_path):
    """DUCKDB_MEMORY_LIMIT is documented; it has to actually be set.

    DuckDB normalises the value, so 1GB comes back as 953.6 MiB.
    """
    settings.duckdb_memory_limit = "1GB"
    con = db.connect(tmp_path / "mem.duckdb")
    try:
        got = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        mib = float(got.split()[0])
        assert mib == pytest.approx(953.67, abs=1.0), f"memory_limit not applied: {got}"
    finally:
        con.close()


def test_duckdb_temp_directory_setting_reaches_the_engine(tmp_path):
    spill = tmp_path / "spill"
    spill.mkdir()
    settings.duckdb_temp_directory = str(spill)
    con = db.connect(tmp_path / "temp.duckdb")
    try:
        got = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
        assert got.endswith("spill"), f"temp_directory was not applied, got {got}"
    finally:
        con.close()


# --- 2. the backfill must not re-read every volume once per rule -------------


def test_backfill_reads_each_volume_once(tmp_path, monkeypatch):
    """One full pass over the child volumes, not one per foreign key rule.

    With ~10 volumes per table and eight rules, the naive version reads the
    same establishments volumes eight times over.
    """
    settings.project_root = tmp_path
    settings.create_dirs()
    for v in (1, 2, 3):
        _write("estabelecimentos", v, [_row("estabelecimentos", ["00000001", "0001", "00", "1", "F", "2", "20240101", "0", "", "150", "20240102", "1234567", "", "", "", "", "", "", "", "", "12345678", "SP", "1234", "", "", "", "", "", "", "", "", "", "20240103"])])
    _write("paises", 1, ["150;Brasil"])
    _write("municipios", 1, ["1234;Cidade"])
    _write("cnaes", 1, ["1234567;Atividade"])

    con = duckdb.connect()
    con.execute(
        (pathlib.Path(__file__).resolve().parents[1] / "src/schema.sql").read_text(
            encoding="utf-8"
        )
    )

    calls = []
    real = db.read_csv_files

    def counting(*args, **kwargs):
        calls.append(args[1])
        return real(*args, **kwargs)

    monkeypatch.setattr(db, "read_csv_files", counting)
    # Drive the two phases production actually runs, with the same cache
    # lifetime, not the single-phase path.
    backfill.run_backfill(
        con, parents=backfill.DOMAIN_PARENTS, keep_cache=True
    )
    backfill.run_backfill(con, parents=backfill.EMPRESA_PARENTS)
    con.close()

    establishments = [c for c in calls if "estabelecimentos" in c]
    assert len(establishments) == 3, (
        f"the three volumes were read {len(establishments)} times; "
        "the backfill should scan them once"
    )


# --- 3. schema lifecycle -----------------------------------------------------


def _write_bytes(config_key: str, volume: int, payload: bytes):
    d = settings.extracted_dir / config_key
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{config_key}{volume}.csv").write_bytes(payload)


def test_a_bom_does_not_corrupt_the_first_value(duckdb_con, tmp_project):
    """strip_bom lost its only consumer when the consolidation step was removed.

    A UTF-8 BOM read as latin-1 becomes the three characters EF BB BF glued to
    the first value, so the first row of every volume would carry a polluted
    primary key.
    """
    import src.database_loader as dl

    settings.gate_min_rows = 1000
    row = _row("empresas", ["00000001", "A"])
    # EF BB BF is the BOM as it appears once decoded as latin-1.
    _write_bytes("empresas", 1, b"\xef\xbb\xbf" + row.encode("latin1") + b"\n")
    dl.schema_validate = lambda n, c: (c, {}, {})
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")
    stored = duckdb_con.execute("SELECT cnpj_basico FROM empresas").fetchall()
    assert stored == [("00000001",)], f"the BOM leaked into the key: {stored}"


def test_crlf_line_endings_do_not_shift_columns(duckdb_con, tmp_project):
    import src.database_loader as dl

    settings.gate_min_rows = 1000
    row = _row("empresas", ["00000001", "A"])
    _write_bytes("empresas", 1, row.encode("latin1") + b"\r\n")
    dl.schema_validate = lambda n, c: (c, {}, {})
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")
    stored = duckdb_con.execute("SELECT cnpj_basico, razao_social FROM empresas").fetchall()
    assert stored == [("00000001", "A")], f"CRLF shifted the columns: {stored}"


def test_quoted_delimiter_inside_a_value_is_parsed(duckdb_con, tmp_project):
    """cnae_fiscal_secundaria carries '11;22,33', quoted in the source."""
    import src.database_loader as dl

    settings.gate_min_rows = 1000
    cols = ETL_CONFIG["estabelecimentos"]["column_names"]
    vals = {
        "cnpj_basico": "00000001",
        "cnpj_ordem": "0001",
        "cnpj_dv": "00",
        "uf": "SP",
        "cnae_fiscal_secundaria": '"11;22,33"',
    }
    _write("estabelecimentos", 1, [";".join(vals.get(c, "") for c in cols)])
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000001','Base')"
    )
    dl.schema_validate = lambda n, c: (c, {}, {})
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "estabelecimentos")
    stored = duckdb_con.execute(
        "SELECT cnpj_basico, uf, cnae_fiscal_secundaria FROM estabelecimentos"
    ).fetchone()
    assert stored[0] == "00000001"
    assert stored[1] == "SP", f"columns shifted, uf ended up as {stored[1]!r}"
    assert stored[2] == "{11,22,33}"


def test_process_and_load_file_is_not_reentrant_by_design(duckdb_con, tmp_project):
    """One call, one table. Rebuilding the schema is run_loader's job.

    Calling the per-table loader twice inserts the same rows twice, which the
    primary key rejects. That is the contract: ALLOW_DROP is honoured by
    run_loader, not here.
    """
    import src.database_loader as dl

    _write("empresas", 1, [_row("empresas", ["00000001", "A"])])
    dl.schema_validate = lambda n, c: (c, {}, {})
    settings.gate_min_rows = 1000

    dl.process_and_load_file(duckdb_con, "empresas")
    assert db.count_rows(duckdb_con, "empresas") == 1
    with pytest.raises(Exception):
        dl.process_and_load_file(duckdb_con, "empresas")


def test_allow_drop_false_keeps_existing_rows(duckdb_con, tmp_project):
    """ALLOW_DROP=false must mean the schema is not rebuilt."""
    import src.database_loader as dl

    _write("empresas", 1, [_row("empresas", ["00000001", "A"])])
    dl.schema_validate = lambda n, c: (c, {}, {})
    settings.gate_min_rows = 1000
    settings.allow_drop = False

    dl.execute_sql_file(duckdb_con, "schema.sql")
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000009','X')"
    )
    dl.execute_sql_file(duckdb_con, "schema.sql")

    assert db.count_rows(duckdb_con, "empresas") == 1, (
        "ALLOW_DROP=false wiped an existing row"
    )


# --- 4. transaction and failure semantics ------------------------------------


def test_failed_ddl_in_a_script_rolls_back_everything(duckdb_con):
    with pytest.raises(Exception):
        db.apply_script(
            duckdb_con,
            "CREATE TABLE a(x INT); CREATE TABLE b(x INT); SELECT * FROM nope;",
        )
    assert db.table_exists(duckdb_con, "a") is False
    assert db.table_exists(duckdb_con, "b") is False


def test_connection_is_usable_after_a_failed_insert(duckdb_con):
    """A rejected INSERT must not leave the connection wedged."""
    duckdb_con.execute(
        "INSERT INTO paises VALUES (150, 'Brasil')"
    )
    with pytest.raises(Exception):
        duckdb_con.execute("INSERT INTO paises VALUES (150, 'Duplicado')")

    # No explicit rollback: the caller may not know one is needed.
    duckdb_con.execute("INSERT INTO paises VALUES (151, 'Outro')")
    assert db.count_rows(duckdb_con, "paises") == 2


def test_transaction_helper_recovers_the_connection(duckdb_con):
    with pytest.raises(Exception):
        with db.transaction(duckdb_con):
            duckdb_con.execute("CREATE TABLE probe (a INT PRIMARY KEY)")
            duckdb_con.execute("INSERT INTO probe VALUES (1)")
            duckdb_con.execute("INSERT INTO probe VALUES (1)")
    duckdb_con.execute("CREATE TABLE after (a INT)")
    assert db.table_exists(duckdb_con, "after") is True


# --- 5. failure modes must be loud -------------------------------------------


def test_missing_domain_file_is_reported(duckdb_con, tmp_project, monkeypatch):
    """No pais file at all: the load must fail loudly, not silently skip."""
    import src.database_loader as dl

    settings.allow_drop = True
    settings.strict_fk_validation = True
    _write("estabelecimentos", 1, [_row("estabelecimentos", ["00000001", "0001", "00", "1", "F", "2", "20240101", "0", "", "150", "20240102", "1234567", "", "", "", "", "", "", "", "", "12345678", "SP", "1234", "", "", "", "", "", "", "", "", "", "20240103"])])
    # no paises/municipios/cnaes written

    dl.clear_domain_cache()
    with pytest.raises(RuntimeError, match="domínio"):
        dl.process_and_load_file(duckdb_con, "estabelecimentos")


def test_orphan_company_is_backfilled_not_rejected(duckdb_con, tmp_project, monkeypatch):
    """An establishment may name a company the empresas file does not contain.

    Losing that case turned a routine gap in the extracts into a load that
    aborted, because the foreign key is enforced on insert and cannot be added
    afterwards.
    """
    import src.database_loader as dl

    settings.gate_min_rows = 1000
    settings.strict_fk_validation = False
    settings.enable_constraints_backfill = True
    cols = ETL_CONFIG["estabelecimentos"]["column_names"]
    vals = {"cnpj_basico": "00009999", "cnpj_ordem": "0001", "cnpj_dv": "00", "uf": "SP"}
    _write("estabelecimentos", 1, [";".join(vals.get(c, "") for c in cols)])
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    dl.clear_domain_cache()

    backfill.run_backfill(duckdb_con)
    dl.process_and_load_file(duckdb_con, "estabelecimentos")

    companies = duckdb_con.execute(
        "SELECT cnpj_basico, razao_social FROM empresas"
    ).fetchall()
    assert companies == [("00009999", backfill.MISSING_EMPRESA)], companies
    assert db.count_rows(duckdb_con, "estabelecimentos") == 1


# --- 6. the engine seam must not leak identifiers -----------------------------


def test_table_name_with_a_quote_is_rejected():
    with pytest.raises(ValueError):
        db.quote_ident('x" ; DROP TABLE empresas; --')


def test_append_dataframe_rejects_an_unexpected_column(duckdb_con):
    frame = pd.DataFrame({"coluna_inexistente": ["1"]})
    with pytest.raises(Exception):
        db.append_dataframe(duckdb_con, frame, "empresas")


# --- 7. multi-volume semantics ------------------------------------------------


def test_a_volume_with_no_rows_is_not_an_error(duckdb_con, tmp_project, monkeypatch):
    import src.database_loader as dl

    _write("empresas", 1, [_row("empresas", ["00000001", "A"])])
    _write("empresas", 2, "")  # an empty volume
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    settings.gate_min_rows = 1000
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")
    assert db.count_rows(duckdb_con, "empresas") == 1


# --- found by running the loader on 8.7M rows --------------------------------


def test_quarantine_serialises_pandas_timestamps(duckdb_con, tmp_project):
    """Date columns are Timestamps, which json.dumps refuses.

    Found on a full-dataset run: every invalid-CNPJ quarantine write raised
    TypeError and the surrounding ``except`` swallowed it, so the audit trail
    wrote nothing at all.
    """
    import pandas as pd
    import src.database_loader as dl

    stamp = pd.Timestamp("2024-01-01 00:00:00")
    payload = {"row": {"quando": stamp, "n": 1}, "reason": "invalid_cnpj"}
    written = dl._dump_json(payload)

    assert "2024-01-01T00:00:00" in written, written
    assert "invalid_cnpj" in written

    settings.gate_min_rows = 1000
    dl._write_jsonl("quarantine", "estabelecimentos", payload)
    files = list(dl._jsonl_dir("quarantine").glob("estabelecimentos*.jsonl"))
    assert files and files[0].stat().st_size > 0, "the quarantine write wrote nothing"


def test_quarantine_serialises_numpy_scalars():
    import src.database_loader as dl

    written = dl._dump_json({"i": np.int64(7), "f": np.float64(1.5), "b": np.bool_(True)})
    assert '"i": 7' in written
    assert '"f": 1.5' in written


def test_duplicate_primary_keys_do_not_abort_the_load(duckdb_con, tmp_project):
    """The Receita repeats establishments, including across volumes.

    DuckDB rejects a duplicate key on insert and the transaction is per chunk,
    so one repeated row aborted the entire load on a real-sized run.
    """
    import json as _json
    import src.database_loader as dl

    settings.gate_min_rows = 1000
    cols = ETL_CONFIG["estabelecimentos"]["column_names"]

    def estab(ordem: str) -> str:
        vals = {
            "cnpj_basico": "00000001",
            "cnpj_ordem": ordem,
            "cnpj_dv": "00",
            "uf": "SP",
        }
        return ";".join(vals.get(c, "") for c in cols)

    _write("estabelecimentos", 1, [estab("0001"), estab("0001")])
    _write("estabelecimentos", 2, [estab("0001"), estab("0002")])
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000001','Base')"
    )
    dl.schema_validate = lambda n, c: (c, {}, {})
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "estabelecimentos")

    rows = duckdb_con.execute(
        "SELECT cnpj_basico, cnpj_ordem FROM estabelecimentos ORDER BY 1,2"
    ).fetchall()
    assert rows == [("00000001", "0001"), ("00000001", "0002")], rows

    summary = _json.loads(
        (dl._jsonl_dir("telemetry") / "estabelecimentos_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["duplicate_key_rows_dropped"] == 2


def test_allow_drop_defaults_to_false():
    """Flipping this default silently changed the operational contract."""
    from src.settings import Settings

    assert Settings().allow_drop is False


def _write_full_dataset(include_estabelecimentos=True, include_empresas=True):
    """Every table the loader plans to touch, as a real month would provide."""
    _write("paises", 1, ["150;Brasil"])
    _write("municipios", 1, ["1234;Cidade"])
    _write("qualificacoes", 1, ["99;Qualificacao"])
    _write("naturezas", 1, ["9999;Natureza"])
    _write("cnaes", 1, ["1234567;Atividade"])
    _write("simples", 1, [_row("simples", ["00000001", "S", "20240101", "", "M", "20240101", ""])])
    _write("socios", 1, [_row("socios", ["00000001", "1", "Socio", "", "99", "20240101", "150"])])
    if include_empresas:
        _write(
            "empresas",
            1,
            [_row("empresas", ["00000001", "Empresa", "9999", "99", "1000,00", "1"])],
        )
    if include_estabelecimentos:
        cols = ETL_CONFIG["estabelecimentos"]["column_names"]
        vals = {
            "cnpj_basico": "00000001", "cnpj_ordem": "0001", "cnpj_dv": "00",
            "pais_codigo": "150", "municipio_codigo": "1234",
            "cnae_fiscal_principal_codigo": "1234567", "uf": "SP",
        }
        _write("estabelecimentos", 1, [";".join(vals.get(c, "") for c in cols)])




def test_only_filter_does_not_empty_the_database(duckdb_con, tmp_project, monkeypatch):
    """--only empresas must not wipe the tables it is not loading.

    The drop ran before the filter was applied, so a partial load emptied every
    table and then failed on a missing domain.
    """
    import src.database_loader as dl

    settings.allow_drop = True
    settings.gate_min_rows = 1000
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    _write_full_dataset()
    dl.clear_domain_cache()
    dl.run_loader()

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        assert db.count_rows(con, "estabelecimentos") == 1
    finally:
        con.close()

    # A filtered reload over a populated database is refused rather than
    # honoured: DuckDB will not drop empresas while estabelecimentos references
    # it, so honouring it would mean emptying tables this run will not refill.
    with pytest.raises(RuntimeError, match="parcial"):
        dl.run_loader(only=["empresas"])

    # And the refusal costs nothing.
    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        for table in ("empresas", "estabelecimentos", "simples", "socios", "paises"):
            assert db.count_rows(con, table) == 1, f"{table} was emptied by a refusal"
        paises = con.execute("SELECT codigo, nome FROM paises").fetchall()
        assert paises == [(150, "Brasil")], paises
    finally:
        con.close()


def test_only_filter_works_on_an_empty_database(duckdb_con, tmp_project, monkeypatch):
    """--only is for loading into a fresh database, which it still supports."""
    import src.database_loader as dl

    settings.allow_drop = True
    settings.gate_min_rows = 1000
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    _write_full_dataset(include_estabelecimentos=False)
    dl.clear_domain_cache()

    dl.run_loader(only=["empresas"])

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        assert db.count_rows(con, "empresas") == 1
        assert db.count_rows(con, "estabelecimentos") == 0
    finally:
        con.close()


def test_a_run_with_missing_input_does_not_drop_anything(tmp_project, monkeypatch):
    """The drop happens before a single row is read.

    A run whose input turned out to be incomplete left an empty database behind,
    which is how a partial resume destroyed a good load.
    """
    import src.database_loader as dl

    settings.allow_drop = True
    settings.gate_min_rows = 1000
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    _write_full_dataset()
    dl.clear_domain_cache()
    dl.run_loader()

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        before = db.count_rows(con, "empresas")
    finally:
        con.close()

    # The empresas volume disappears, as it would after a partial extraction.
    for stale in (settings.extracted_dir / "empresas").glob("*.csv"):
        stale.unlink()

    with pytest.raises(FileNotFoundError):
        dl.run_loader()

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        assert db.count_rows(con, "empresas") == before, (
            "the run aborted but had already emptied the table"
        )
    finally:
        con.close()


def test_missing_optional_tables_do_not_block_a_fresh_load(duckdb_con, tmp_project, monkeypatch):
    """A month that ships no Simples volume is normal, not a failure."""
    import src.database_loader as dl

    settings.allow_drop = True
    settings.gate_min_rows = 1000
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    _write_full_dataset()
    (settings.extracted_dir / "simples" / "simples1.csv").unlink()
    dl.clear_domain_cache()

    dl.run_loader()

    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        assert db.count_rows(con, "empresas") == 1
        assert db.count_rows(con, "simples") == 0
    finally:
        con.close()


def test_socios_keeps_the_same_person_twice(duckdb_con, tmp_project, monkeypatch):
    """A company lists a person twice on purpose.

    One row as a partner (identificador 2) and one as a legal representative
    (10). Treating (cnpj_basico, cnpj_cpf_socio) as a key dropped the second and
    every later one -- on the highest-volume table in the dataset.
    """
    import src.database_loader as dl

    settings.gate_min_rows = 1000
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    cols = ETL_CONFIG["socios"]["column_names"]

    def socio(identificador: str, cpf: str) -> str:
        vals = {
            "cnpj_basico": "00000001",
            "identificador_socio": identificador,
            "cnpj_cpf_socio": cpf,
            "nome_socio_ou_razao_social": "Maria Silva",
            "qualificacao_socio_codigo": "99",
            "pais_codigo": "150",
        }
        return ";".join(vals.get(c, "") for c in cols)

    _write("socios", 1, [socio("2", "11122233344"), socio("10", "11122233344")])
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000001','Base')"
    )
    duckdb_con.execute("INSERT INTO paises VALUES (150, 'Brasil')")
    duckdb_con.execute("INSERT INTO qualificacoes_socios VALUES (99, 'Q')")
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "socios")

    rows = duckdb_con.execute(
        "SELECT identificador_socio FROM socios ORDER BY 1"
    ).fetchall()
    assert rows == [(2,), (10,)], f"a legitimate second entry was dropped: {rows}"

    summary = json.loads(
        (dl._jsonl_dir("telemetry") / "socios_summary.json").read_text(encoding="utf-8")
    )
    assert summary["duplicate_key_rows_dropped"] == 0


def test_a_foreign_partner_without_a_cpf_is_kept(duckdb_con, tmp_project, monkeypatch):
    """cnpj_cpf_socio is nullable and a foreign partner has none.

    Treating it as a critical field dropped the whole row for a constraint the
    database does not have.
    """
    import src.database_loader as dl

    settings.gate_min_rows = 1000
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    cols = ETL_CONFIG["socios"]["column_names"]
    vals = {
        "cnpj_basico": "00000001",
        "identificador_socio": "2",
        "cnpj_cpf_socio": "",
        "nome_socio_ou_razao_social": "Foreign Partner Ltd",
        "qualificacao_socio_codigo": "99",
        "pais_codigo": "999",
    }
    _write("socios", 1, [";".join(vals.get(c, "") for c in cols)])
    duckdb_con.execute(
        "INSERT INTO empresas (cnpj_basico, razao_social) VALUES ('00000001','Base')"
    )
    duckdb_con.execute("INSERT INTO paises VALUES (999, 'Outro Pais')")
    duckdb_con.execute("INSERT INTO qualificacoes_socios VALUES (99, 'Q')")
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "socios")

    rows = duckdb_con.execute(
        "SELECT nome_socio_ou_razao_social, cnpj_cpf_socio FROM socios"
    ).fetchall()
    assert rows == [("Foreign Partner Ltd", None)], rows


def test_a_table_without_a_declared_key_is_not_deduplicated():
    """The dedup must follow schema.sql, not the critical-field list."""
    import src.database_loader as dl

    assert dl.ETL_CONFIG["socios"].get("primary_key_columns") is None
    assert dl.ETL_CONFIG["empresas"]["primary_key_columns"] == ["cnpj_basico"]
    assert dl.ETL_CONFIG["estabelecimentos"]["primary_key_columns"] == [
        "cnpj_basico",
        "cnpj_ordem",
        "cnpj_dv",
    ]


def test_one_all_null_chunk_does_not_abandon_the_rest(duckdb_con, tmp_project, monkeypatch):
    """A chunk that is entirely unusable must not end the table."""
    import src.database_loader as dl

    settings.gate_min_rows = 1000
    settings.chunk_size = 1
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))

    _write("empresas", 1, [_row("empresas", ["", "SemCodigo"])])
    _write("empresas", 2, [_row("empresas", ["00000007", "Depois"])])
    dl.clear_domain_cache()

    dl.process_and_load_file(duckdb_con, "empresas")
    rows = duckdb_con.execute("SELECT cnpj_basico FROM empresas").fetchall()
    assert rows == [("00000007",)], f"a later volume was abandoned: {rows}"


def test_reload_with_allow_drop_false_refuses_cleanly(tmp_project, monkeypatch):
    """The default cannot silently duplicate rows.

    With populated tables and ALLOW_DROP=false, every second insert collides on
    the primary key. Saying "tables will be reused" and then aborting on the
    first duplicate key is the worst of both.
    """
    import src.database_loader as dl

    settings.allow_drop = False
    settings.gate_min_rows = 1000
    monkeypatch.setattr(dl, "schema_validate", lambda n, c: (c, {}, {}))
    _write_full_dataset()
    dl.clear_domain_cache()

    dl.run_loader()

    with pytest.raises(RuntimeError, match="ALLOW_DROP"):
        dl.run_loader()

    # And the data survived the refusal.
    con = db.connect(settings.duckdb_file, read_only=True)
    try:
        assert db.count_rows(con, "empresas") == 1
    finally:
        con.close()


def test_a_zero_byte_volume_counts_as_missing_input(tmp_project):
    """A truncated download leaves an empty file, not a usable volume."""
    import src.database_loader as dl

    _write_full_dataset()
    for volume in (settings.extracted_dir / "simples").glob("*.csv"):
        volume.unlink()
    (settings.extracted_dir / "simples" / "simples1.csv").write_text("", encoding="utf-8")

    missing = dl._volumes_available(["simples", "empresas"])
    assert missing == ["simples"], missing


def test_dedup_window_is_bounded():
    """An unbounded set grew with the table: about 16 GB at real volume."""
    from src import database_loader as dlm

    assert dlm._DEDUP_WINDOW <= 5_000_000, (
        "the duplicate filter must be bounded, not proportional to the table"
    )


def test_nat_is_written_as_null_not_the_string_nat():
    """Every null date in the audit trail was the literal string "NaT"."""
    import src.database_loader as dl

    assert '"d": null' in dl._dump_json({"d": pd.NaT}), dl._dump_json({"d": pd.NaT})


def test_a_numpy_array_cell_does_not_abort_the_write():
    """pd.isna returns an array for array input; `if` on that raised."""
    import src.database_loader as dl

    written = dl._dump_json({"arr": np.array([1, 2, 3])})
    assert "[1, 2, 3]" in written or "[1,2,3]" in written, written


def test_drop_order_covers_every_table_in_the_schema():
    """A table missing from _DROP_ORDER survives an ALLOW_DROP rebuild.

    The drops are what make a schema change take effect; a table that is added to
    schema.sql but forgotten here keeps its old shape and its old rows, and
    nothing warns about it.
    """
    import re

    import src.database_loader as dl

    schema = (pathlib.Path(__file__).resolve().parents[1] / "src" / "schema.sql").read_text(
        encoding="utf-8"
    )
    tabelas = set(re.findall(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+\"?(\w+)\"?", schema, re.I))

    faltando = tabelas - set(dl._DROP_ORDER)
    assert not faltando, (
        f"tabela(s) no schema.sql fora do _DROP_ORDER: {sorted(faltando)}; "
        "com ALLOW_DROP elas sobrevivem ao rebuild"
    )


def test_drop_order_drops_children_before_parents():
    """FK constraints mean the order is load-bearing, not cosmetic."""
    import src.database_loader as dl

    ordem = list(dl._DROP_ORDER)
    pos = {t: i for i, t in enumerate(ordem)}

    for filho, pai in (
        ("socios", "empresas"),
        ("estabelecimentos", "empresas"),
        ("estabelecimentos", "municipios"),
        ("estabelecimentos", "paises"),
        ("simples", "empresas"),
    ):
        assert pos[filho] < pos[pai], f"{filho} e derrubado depois de {pai}"
