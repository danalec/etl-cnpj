"""Database access for DuckDB.

The pipeline talks to the database through a handful of operations: run a
script, append a DataFrame, read domain values, wrap a unit of work in a
transaction. Everything DuckDB-specific lives here so the loader keeps talking
in terms of those operations.

Two DuckDB behaviours shape this module and are easy to get wrong:

- A multi-statement script is **not** atomic. Each statement commits on its own,
  so a failure mid-script leaves earlier statements applied. Anything that must
  roll back as a unit has to say so explicitly.
- Table and column names cannot be parameterised, so they are escaped with
  :func:`quote_ident`. Every name reaching here comes from ``ETL_CONFIG``, never
  from row data.
"""

from __future__ import annotations

import itertools
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

# id() is reused once an object is collected, so two chunks could otherwise end
# up sharing a view name.
_view_counter = itertools.count()

# DuckDB keeps the identifier verbatim inside the quotes; this rejects the
# characters that could break out of them.
_FORBIDDEN_IN_IDENT = '"'


def quote_ident(name: str) -> str:
    if _FORBIDDEN_IN_IDENT in name:
        raise ValueError(f"illegal identifier: {name!r}")
    return f'"{name}"'


def connect(path: str | Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the database, creating the parent directory if needed.

    The DuckDB knobs are applied here rather than at each call site, so a
    setting in ``.env`` actually reaches the engine. Anything left unset keeps
    the DuckDB default.
    """
    from .settings import settings

    path = Path(path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)

    if read_only:
        # These are not settable on a read-only handle.
        con.execute("SET TimeZone='UTC'")
        return con

    con.execute("SET TimeZone='UTC'")

    threads = getattr(settings, "duckdb_threads", 0)
    if threads:
        con.execute(f"SET threads={int(threads)}")

    memory_limit = getattr(settings, "duckdb_memory_limit", "")
    if memory_limit:
        con.execute(f"SET memory_limit={memory_limit!r}")

    temp_directory = getattr(settings, "duckdb_temp_directory", "")
    if temp_directory:
        Path(temp_directory).mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory={str(temp_directory)!r}")

    return con


@contextmanager
def transaction(con: duckdb.DuckDBPyConnection) -> Iterator[duckdb.DuckDBPyConnection]:
    """Run a unit of work that must succeed or leave nothing behind.

    After a failed statement DuckDB marks the transaction aborted and refuses
    every further query until ``ROLLBACK``. That is why the rollback happens in
    the ``finally`` rather than only on the error path.
    """
    con.begin()
    try:
        yield con
    except Exception:
        try:
            con.rollback()
        except Exception:  # pragma: no cover - rollback of a dead transaction
            logger.debug("rollback falhou; a transação já estava encerrada")
        raise
    else:
        con.commit()


def apply_script(con: duckdb.DuckDBPyConnection, sql_text: str) -> None:
    """Run a multi-statement SQL file as one unit of work.

    DuckDB commits statement by statement, so without the explicit transaction a
    failure halfway through leaves the earlier statements applied.
    """
    with transaction(con):
        con.execute(sql_text)


def append_dataframe(
    con: duckdb.DuckDBPyConnection, df: pd.DataFrame, table: str
) -> int:
    """Insert a DataFrame into ``table`` by column name.

    DuckDB can register an in-memory object as a relation and select straight
    out of it, which avoids serialising the chunk to CSV and back.
    """
    if df.empty:
        return 0
    target = quote_ident(table)
    view = f"_chunk_{next(_view_counter):x}"
    con.register(view, df)
    try:
        columns = ", ".join(quote_ident(c) for c in df.columns)
        con.execute(f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {quote_ident(view)}")
    finally:
        con.unregister(view)
    return len(df)


def distinct_values(
    con: duckdb.DuckDBPyConnection, table: str, column: str
) -> set[Any]:
    """The distinct, non-null values of ``table.column``."""
    sql = (
        f"SELECT DISTINCT {quote_ident(column)} FROM {quote_ident(table)} "
        f"WHERE {quote_ident(column)} IS NOT NULL"
    )
    return {row[0] for row in con.execute(sql).fetchall()}


def count_rows(con: duckdb.DuckDBPyConnection, table: str) -> int:
    row = con.execute(f"SELECT count(*) FROM {quote_ident(table)}").fetchone()
    return int(row[0]) if row else 0


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [table]
    ).fetchone()
    return bool(row and row[0])


def constraint_names(
    con: duckdb.DuckDBPyConnection, table: str | None = None
) -> set[str]:
    if table is None:
        rows = con.execute(
            "SELECT constraint_name FROM duckdb_constraints()"
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT constraint_name FROM duckdb_constraints() WHERE table_name = ?",
            [table],
        ).fetchall()
    return {r[0] for r in rows if r[0]}


def column_is_numeric(con: duckdb.DuckDBPyConnection, table: str, column: str) -> bool:
    """Whether a column holds numbers, per the catalog rather than the data.

    Guessing from the rows fails on an empty table: an all-numeric test over an
    empty set is vacuously true, which would coerce the zero-padded cnpj_basico
    to a number and create parents that can never satisfy the foreign key.
    """
    row = con.execute(
        "SELECT data_type FROM duckdb_columns() WHERE table_name = ? AND column_name = ?",
        [table, column],
    ).fetchone()
    if not row:
        return False
    return "INT" in str(row[0]).upper() or "DECIMAL" in str(row[0]).upper()


def index_names(con: duckdb.DuckDBPyConnection, table: str | None = None) -> set[str]:
    if table is None:
        rows = con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
    else:
        rows = con.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE table_name = ?", [table]
        ).fetchall()
    return {r[0] for r in rows if r[0]}


def read_csv_files(
    con: duckdb.DuckDBPyConnection,
    pattern: str,
    *,
    columns: Iterable[str],
    select: Iterable[str] | None = None,
    types: dict[str, str] | None = None,
    encoding: str = "latin-1",
) -> pd.DataFrame:
    """Read delimited Receita Federal volumes matching ``pattern``.

    ``columns`` must be the full positional width of the file: DuckDB matches
    the ``columns`` map against the column count and rejects a mismatch.
    ``select`` narrows the result afterwards, so a caller that wants a single
    field still has to describe the whole layout.
    """
    names = list(columns)
    wanted = [n for n in (select or names) if n in names]
    sql, params = csv_source_sql(
        pattern,
        columns=names,
        types=types,
        encoding=encoding,
        strip_bom=False,
        union_by_name=True,
    )
    projection = ", ".join(quote_ident(n) for n in wanted)
    return con.execute(f"SELECT {projection} FROM ({sql})", params).df()


# O BOM UTF-8 (EF BB BF) lido como latin-1 nao vira U+FEFF: cada byte vira um
# caractere latin-1, "ï»¿". Foi verificado executando o leitor, porque a
# suposicao natural (ltrim com U+FEFF) falha em silencio e sobbem o primeiro
# valor da tabela. E o mesmo motivo pelo qual _open_volume remove os bytes crus
# antes de decodificar no caminho pandas.
BOM_LATIN1 = "\u00ef\u00bb\u00bf"

# settings.file_encoding says "latin1" and the CSV reader wants "latin-1"; it
# rejects the first with a wall of supported-encoding names. Normalising here
# keeps the .env spelling that pandas always accepted.
_ENCODING_ALIASES = {
    "latin1": "latin-1",
    "latin_1": "latin-1",
    "iso88591": "latin-1",
    "iso_8859_1": "latin-1",
    "utf8": "utf-8",
    "cp1252": "windows-1252",
}


def normalize_encoding(name: str) -> str:
    key = str(name).strip().lower().replace(" ", "")
    return _ENCODING_ALIASES.get(key, key)


def csv_source_sql(
    path: str | Path,
    *,
    columns: Iterable[str],
    types: dict[str, str] | None = None,
    encoding: str = "latin-1",
    strip_bom: bool = True,
    union_by_name: bool = False,
    cast_types: dict[str, str] | None = None,
) -> tuple[str, list]:
    """The SELECT that reads one extracted volume, plus its query parameters.

    Returned as text rather than as a relation, on purpose. ``rel.select(...)``
    and ``rel.sql_query()`` both force the volume to be materialised into the
    client -- ``sql_query()`` serialises the rows literally into the string --
    which for a Receita file means dragging the whole thing through Python, the
    exact cost this path exists to remove. Building the SELECT once and letting
    DuckDB execute it keeps the read lazy and parallel.

    ``columns`` must be the full positional width of the file, because DuckDB
    matches the column map against the column count and rejects a mismatch.
    """
    types = types or {}
    names = list(columns)
    layout = ", ".join(f"{quote_ident(n)}: {types.get(n, 'VARCHAR')}" for n in names)

    projection = []
    for i, n in enumerate(names):
        ref = quote_ident(n)
        # The BOM rides on the first field of the first row. ltrim is a no-op
        # on every other row, so it does not need to know which one it landed on.
        if strip_bom and i == 0:
            ref = f"ltrim({ref}, '{BOM_LATIN1}')"
        target = (cast_types or {}).get(n, "").upper()
        # The CSV arrives as text. An empty field has to become NULL, the way
        # pandas read it, instead of failing the cast. A genuinely non-numeric
        # value still raises, exactly as the pandas path did.
        if target and target != "VARCHAR":
            ref = f"CAST(NULLIF({ref}, '') AS {target})"
        projection.append(f"{ref} AS {quote_ident(n)}")

    # A função SQL read_csv, e não con.read_csv(): o método Python desce por
    # COPY, que só aceita UTF-8, e o material da Receita é latin-1.
    sql = (
        f"SELECT {', '.join(projection)} FROM read_csv("
        f"?, delim=';', header=false, columns={{{layout}}}, "
        f"encoding=?, union_by_name=?, ignore_errors=false)"
    )
    return sql, [str(path), normalize_encoding(encoding), union_by_name]


def count_source(
    con: duckdb.DuckDBPyConnection, source_sql: str, params: list | None = None
) -> int:
    """Row count of a source SELECT, evaluated in the engine."""
    row = con.execute(f"SELECT count(*) FROM ({source_sql})", params or []).fetchone()
    return int(row[0]) if row else 0


def table_column_types(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    """Column name -> declared type, straight from the catalog."""
    rows = con.execute(
        "SELECT column_name, data_type FROM duckdb_columns() WHERE table_name = ?",
        [table],
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def column_stats_sql(source_sql: str, columns: Iterable[str]) -> tuple[str, list[str]]:
    """SQL returning null counts and min/max per column, plus the aliases used.

    Min/max are taken on the cast column, not on the raw text: comparing "20"
    against "105" as strings orders them the wrong way round, and the pandas
    path compared them as numbers because the reader had inferred a type.
    """
    names = list(columns)
    parts = []
    aliases = []
    for name in names:
        ref = quote_ident(name)
        alias = f"{name}__nulls"
        parts.append(f"count(*) FILTER (WHERE {ref} IS NULL) AS {alias}")
        aliases.append(alias)
        parts.append(f"min({ref})")
        parts.append(f"max({ref})")
    return f"SELECT {', '.join(parts)} FROM {source_sql}", aliases


def insert_source(
    con: duckdb.DuckDBPyConnection,
    source_sql: str,
    params: list | None,
    table: str,
    columns: Iterable[str],
) -> int:
    """``INSERT OR IGNORE`` a source SELECT straight into ``table``; rows landed.

    ``OR IGNORE`` covers exactly the primary-key conflict that
    ``_drop_duplicate_keys`` used to handle in Python, and keeps the first
    occurrence the way that helper did. A NOT NULL violation is *not* covered,
    so a row with a null key still aborts the load -- which is what happened
    before as well.

    The landed count comes from the difference in table cardinality rather than
    ``RETURNING``: returning one row per inserted row would materialise the whole
    load in the client, which is exactly the cost this path exists to avoid. It
    is a count over the target, so it is meant for the small tables that take
    this path -- the big ones still go through pandas.
    """
    cols = ", ".join(quote_ident(c) for c in columns)
    before = count_rows(con, table)
    con.execute(
        f"INSERT OR IGNORE INTO {quote_ident(table)} ({cols}) "
        f"SELECT {cols} FROM ({source_sql})",
        params or [],
    )
    return count_rows(con, table) - before