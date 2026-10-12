"""Backfill of missing parent rows, before any child is loaded.

DuckDB enforces foreign keys during the INSERT and cannot add them later, so an
orphan reference aborts the load. The Receita extracts are not referentially
complete: ``estabelecimentos`` references ``paises`` codes that may not exist in
the ``paises`` file.

This module runs between the domain load and the child load: it scans the child
volumes for values that would violate a foreign key and creates the synthetic
parents first, so a missing reference is visible in the data rather than
silently dropped.
"""

from __future__ import annotations

import logging

import pandas as pd

from . import db
from .settings import settings

logger = logging.getLogger(__name__)

MISSING_PARENT = "NAO CONSTA NA ORIGEM"

# Synthetic parent row for the company itself, when an estabelecimento points at
# a cnpj_basico that the empresas file does not contain.
MISSING_EMPRESA = "EMPRESA INEXISTENTE NA ORIGEM - GERADA AUTOMATICAMENTE"

# (child config, child column, parent table, parent key, parent name, label)
# The first element is the ETL_CONFIG key, which is also the directory name
# under extracted_files/.
#
# The parent rows for `empresas` matter as much as the domain lookups: an
# estabelecimento, a Simples entry or a partner can name a company that the
# empresas file does not contain, and losing that row turned a routine gap in
# the extracts into a load that aborts on the first orphan.
BACKFILL_RULES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("empresas", "natureza_juridica_codigo", "naturezas_juridicas",
     "codigo", "nome", MISSING_PARENT),
    ("empresas", "qualificacao_responsavel", "qualificacoes_socios",
     "codigo", "nome", MISSING_PARENT),
    ("estabelecimentos", "pais_codigo", "paises",
     "codigo", "nome", MISSING_PARENT),
    ("estabelecimentos", "municipio_codigo", "municipios",
     "codigo", "nome", MISSING_PARENT),
    ("estabelecimentos", "cnae_fiscal_principal_codigo", "cnaes",
     "codigo", "nome", MISSING_PARENT),
    ("estabelecimentos", "cnpj_basico", "empresas",
     "cnpj_basico", "razao_social", MISSING_EMPRESA),
    ("socios", "pais_codigo", "paises",
     "codigo", "nome", MISSING_PARENT),
    ("socios", "qualificacao_socio_codigo", "qualificacoes_socios",
     "codigo", "nome", MISSING_PARENT),
    ("socios", "qualificacao_representante_legal_codigo", "qualificacoes_socios",
     "codigo", "nome", MISSING_PARENT),
    ("socios", "cnpj_basico", "empresas",
     "cnpj_basico", "razao_social", MISSING_EMPRESA),
    ("simples", "cnpj_basico", "empresas",
     "cnpj_basico", "razao_social", MISSING_EMPRESA),
)


def _as_key(series: pd.Series, numeric: bool) -> set:
    """Normalise a raw CSV column to the parent's key type.

    Values arrive as text because the loader reads the volumes as VARCHAR. Domain
    codes (pais, cnae, natureza) are numeric, but ``cnpj_basico`` is zero-padded
    text: coercing it to a number would turn "00009999" into 9999, create a
    parent that does not match, and leave the foreign key failing.
    """
    if not numeric:
        return {str(v).strip() for v in series.dropna() if str(v).strip()}
    coerced = pd.to_numeric(series, errors="coerce").dropna()
    return set(coerced.tolist())


def _columns_for(config_key: str) -> set:
    """Every column any rule reads from ``config_key``'s volumes."""
    return {
        rule[1] for rule in BACKFILL_RULES if rule[0] == config_key
    }


def scan_child_values(
    con, config_key: str, columns: list[str]
) -> dict[str, set]:
    """Distinct values of ``columns`` across every volume of a child table.

    One pass per child table, not one per foreign key. A full run reads the
    ``estabelecimentos`` volumes three times instead of once otherwise, which at
    the real volume size is the difference between seconds and minutes of I/O.
    """
    from .database_loader import ETL_CONFIG

    cfg = ETL_CONFIG.get(config_key)
    if cfg is None:
        return {c: set() for c in columns}

    directory = settings.extracted_dir / config_key
    if not directory.is_dir():
        return {c: set() for c in columns}

    # Both phases read the same volumes and the volumes do not change between
    # them. They also want different columns of the same table, so the first
    # scan always reads the union of every column any rule needs and the later
    # phase reuses it -- otherwise establishments and socios get read twice.
    wanted = sorted(_columns_for(config_key))
    cached = _SCAN_CACHE.get(config_key)
    if cached is not None and set(wanted) <= set(cached):
        return {c: cached[c] for c in columns}

    columns = wanted
    collected: dict[str, set] = {c: set() for c in columns}
    for csv_path in sorted(directory.glob("*.csv")):
        if csv_path.stat().st_size == 0:
            # A zero-byte volume is benign in a partial download.
            continue
        try:
            frame = db.read_csv_files(
                con,
                str(csv_path),
                columns=cfg["column_names"],
                select=columns,
            )
        except Exception:
            logger.warning("backfill: falha lendo %s", csv_path.name, exc_info=True)
            continue
        if frame.empty:
            continue
        for column in columns:
            if column in frame.columns:
                collected[column] |= _as_key(frame[column], numeric=False)
    _SCAN_CACHE[config_key] = collected
    return {c: collected[c] for c in columns if c in collected}


def find_orphan_keys(
    con,
    config_key: str,
    child_column: str,
    parent_table: str,
    parent_column: str,
    scanned: dict[str, set] | None = None,
) -> list:
    """Values present in the child volumes with no matching parent row."""
    parent_keys = db.distinct_values(con, parent_table, parent_column)
    if scanned is not None:
        values = set(scanned.get(child_column, set()))
    else:
        values = scan_child_values(con, config_key, [child_column]).get(
            child_column, set()
        )
    if not values:
        return []

    # Domain codes are numeric in the database and text in the CSV; cnpj_basico is
    # zero-padded text on both sides. The catalog decides which, so an empty
    # parent table cannot flip the comparison to the wrong form.
    if db.column_is_numeric(con, parent_table, parent_column):
        numeric_child = _as_key(pd.Series(sorted(values)), numeric=True)
        numeric_parent = {
            int(k) for k in parent_keys if isinstance(k, (int, float))
        }
        return sorted(numeric_child - numeric_parent)

    text_parent = {str(k).strip() for k in parent_keys}
    return sorted(values - text_parent)


def _insert_missing_parents(
    con,
    parent_table: str,
    parent_column: str,
    name_column: str,
    keys: list,
    label: str,
) -> int:
    if not keys:
        return 0
    parent = db.quote_ident(parent_table)
    column = db.quote_ident(parent_column)
    name = db.quote_ident(name_column)
    con.register("_backfill_keys", pd.DataFrame({parent_column: keys}))
    try:
        with db.transaction(con):
            con.execute(
                f"""
                INSERT INTO {parent} ({column}, {name})
                SELECT {column}, ?
                FROM _backfill_keys
                WHERE NOT EXISTS (
                    SELECT 1 FROM {parent} WHERE {column} = _backfill_keys.{column}
                )
                """,
                [label],
            )
    finally:
        con.unregister("_backfill_keys")
    logger.info(
        "backfill: %d chaves sinteticas em %s.%s",
        len(keys),
        parent_table,
        parent_column,
    )
    return len(keys)


# Cleared at the start of every load; the cache lives for one run_loader.
_SCAN_CACHE: dict[str, dict[str, set]] = {}

DOMAIN_PARENTS = (
    "paises",
    "municipios",
    "cnaes",
    "naturezas_juridicas",
    "qualificacoes_socios",
)
EMPRESA_PARENTS = ("empresas",)


def run_backfill(
    con,
    parents: tuple[str, ...] | None = None,
    keep_cache: bool = False,
) -> dict[str, int]:
    """Create every parent row the child volumes reference but do not supply.

    ``parents`` restricts which rules run. It exists because the rules cannot all
    run at the same moment: the domain lookups are referenced by ``empresas``, so
    they must be filled before it loads, while the synthetic companies are
    referenced by the tables that load after it.

    ``keep_cache`` keeps the scan results for the caller. The two phases in
    run_loader read the same volumes, so the first keeps the scan and the second
    reuses it; every other caller gets a clean cache rather than stale data from
    a previous call.

    Returns the number of synthetic rows created per table, for the log summary.
    """
    # Group the rules by child table so each set of volumes is scanned once.
    by_child: dict[str, list] = {}
    for rule in BACKFILL_RULES:
        if parents is not None and rule[2] not in parents:
            continue
        by_child.setdefault(rule[0], []).append(rule)

    created: dict[str, int] = {}
    total_scans = 0

    for config_key, rules in by_child.items():
        columns = [rule[1] for rule in rules]
        scanned = scan_child_values(con, config_key, columns)
        total_scans += 1

        for _, column, parent, parent_column, name_column, label in rules:
            orphans = find_orphan_keys(
                con, config_key, column, parent, parent_column, scanned=scanned
            )
            if not orphans:
                continue
            try:
                created[parent] = created.get(parent, 0) + _insert_missing_parents(
                    con, parent, parent_column, name_column, orphans, label
                )
            except Exception:
                logger.exception(
                    "backfill: nao foi possivel criar %s de %s", column, parent
                )

    logger.debug("backfill: %d varredura(s) de volumes", total_scans)
    if not keep_cache:
        _SCAN_CACHE.clear()
    return created
