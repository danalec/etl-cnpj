"""The chunk writer, against a real database.

DuckDB is embedded, so the test needs no service and no gate: it registers the
DataFrame and selects straight into the table.
"""

import pandas as pd
import pytest

from src import db
from src.database_loader import fast_load_chunk


def test_fast_load_chunk_round_trip(duckdb_con):
    frame = pd.DataFrame(
        {
            "cnpj_basico": pd.Series(["00000001", "00000002"], dtype="string"),
            "razao_social": pd.Series(["Empresa A", "Empresa B"], dtype="string"),
            "capital_social": pd.Series(["1000.00", "2500.50"], dtype="object"),
        }
    )
    fast_load_chunk(duckdb_con, frame, "empresas")

    assert db.count_rows(duckdb_con, "empresas") == 2
    rows = duckdb_con.execute(
        "SELECT cnpj_basico, razao_social, capital_social FROM empresas ORDER BY 1"
    ).fetchall()
    assert rows == [("00000001", "Empresa A", 1000.00), ("00000002", "Empresa B", 2500.50)]


def test_fast_load_chunk_is_incremental(duckdb_con):
    for i in range(3):
        fast_load_chunk(
            duckdb_con,
            pd.DataFrame(
                {
                    "cnpj_basico": pd.Series([f"0000000{i}"], dtype="string"),
                    "razao_social": pd.Series([f"E{i}"], dtype="string"),
                }
            ),
            "empresas",
        )
    assert db.count_rows(duckdb_con, "empresas") == 3


def test_fast_load_chunk_preserves_nulls(duckdb_con):
    frame = pd.DataFrame(
        {
            "cnpj_basico": pd.Series(["00000001"], dtype="string"),
            "razao_social": pd.Series([None], dtype="object"),
        }
    )
    fast_load_chunk(duckdb_con, frame, "empresas")
    assert duckdb_con.execute(
        "SELECT razao_social FROM empresas WHERE cnpj_basico='00000001'"
    ).fetchone()[0] is None


def test_fast_load_chunk_rejects_a_conflicting_row(duckdb_con):
    fast_load_chunk(
        duckdb_con,
        pd.DataFrame(
            {
                "cnpj_basico": pd.Series(["00000001"], dtype="string"),
                "razao_social": pd.Series(["A"], dtype="string"),
            }
        ),
        "empresas",
    )
    with pytest.raises(Exception):
        fast_load_chunk(
            duckdb_con,
            pd.DataFrame(
                {
                    "cnpj_basico": pd.Series(["00000001"], dtype="string"),
                    "razao_social": pd.Series(["B"], dtype="string"),
                }
            ),
            "empresas",
        )
    assert duckdb_con.execute("SELECT razao_social FROM empresas").fetchone()[0] == "A"