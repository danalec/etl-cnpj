import collections
import pandas as pd
import logging
import re
import json
from datetime import datetime, timezone
import requests
from pathlib import Path
from typing import Dict, Tuple, Set, Union, cast, Any, TypedDict, Callable, Optional
from . import backfill, db
from .settings import settings, UF_SET
from .validation import validate as schema_validate

logger = logging.getLogger(__name__)

# --- Funções de Carga e Limpeza ---


def fast_load_chunk(conn, df, table_name):
    """Write one validated chunk to the database.

    The chunk is registered as an in-memory relation and selected straight into
    the table, so the data is never serialised to an intermediate format.
    """
    try:
        with db.transaction(conn):
            db.append_dataframe(conn, df, table_name)
    except Exception as e:
        logger.error(f"Erro ao gravar na tabela {table_name}: {e}")
        raise


DOMAIN_CACHE: Dict[Tuple[str, str], Set[Union[int, str]]] = {}


def _get_domain_set(conn, table, column):
    key = (table, column)
    if key not in DOMAIN_CACHE:
        DOMAIN_CACHE[key] = db.distinct_values(conn, table, column)
    return DOMAIN_CACHE[key]


def _ensure_domains_loaded(conn, domains, strict):
    for tbl in domains:
        if db.count_rows(conn, tbl) == 0:
            if strict:
                raise RuntimeError(f"Tabela de domínio '{tbl}' ainda não carregada. Ajuste a ordem ou remova filtros --only/--exclude.")
            else:
                logger.warning(f"Tabela de domínio '{tbl}' vazia. Validação de FK será relaxada.")


def _validate_fk_set(df, column, valid_set, label, strict):
    if column not in df.columns:
        return None
    mask_valid = df[column].isin(valid_set)
    mask_invalid = (~mask_valid) & df[column].notna()
    if mask_invalid.any():
        if strict:
            sample = list(set(df.loc[mask_invalid, column].astype(object).tolist()))[:10]
            # Return mask to allow caller to quarantine before raising
            raise ValueError(f"Valores inválidos em '{label}': {sample} (total {int(mask_invalid.sum())}). Garanta que a tabela de domínio está carregada e os códigos são válidos.")
        else:
            df.loc[mask_invalid, column] = pd.NA
            logger.warning(f"Valores inválidos em '{label}' foram definidos como nulos. Total {int(mask_invalid.sum())}.")
    return mask_invalid


def validate_chunk(config_name, chunk_df, conn):
    fk_violations = {}
    req = DOMAINS_REQUIRED.get(config_name, [])
    _ensure_domains_loaded(conn, req, settings.strict_fk_validation)
    domain_cache: Dict[str, Set[Any]] = {}
    for col, tbl, colname in FK_RULES.get(config_name, []):
        if col not in chunk_df.columns:
            continue
        if col == "uf":
            m = _validate_fk_set(chunk_df, "uf", UF_SET, "uf", settings.strict_fk_validation)
            if m is not None and m.any():
                fk_violations["uf"] = m
            continue
        if tbl and colname:
            if tbl not in domain_cache:
                domain_cache[tbl] = _get_domain_set(conn, tbl, colname)
            s = domain_cache[tbl]
            m = _validate_fk_set(chunk_df, col, s, col, settings.strict_fk_validation)
            if m is not None and m.any():
                fk_violations[col] = m
    if config_name == "estabelecimentos" and "cnae_fiscal_secundaria" in chunk_df.columns:
        s = chunk_df["cnae_fiscal_secundaria"].dropna().astype(str)
        bad = ~((s.str.startswith("{") & s.str.endswith("}")) | (s == ""))
        if bad.any():
            if settings.strict_fk_validation:
                raise ValueError("Campo cnae_fiscal_secundaria com formato inválido em algumas linhas. Esperado o literal de chaves da Receita: '{...}'.")
            else:
                chunk_df.loc[bad.index[bad], "cnae_fiscal_secundaria"] = None
                logger.warning("Valores inválidos em cnae_fiscal_secundaria foram definidos como nulos.")
                fk_violations["cnae_fiscal_secundaria_fmt"] = bad
    return fk_violations


def sanitize_dates(df, date_columns):
    cols = [c for c in date_columns if c in df.columns]
    if cols:
        df[cols] = df[cols].apply(lambda s: pd.to_datetime(s, format="%Y%m%d", errors="coerce"))
    return df


def clean_empresas_chunk(chunk_df):
    if "capital_social" in chunk_df.columns:
        s = chunk_df["capital_social"].astype(str).str.strip()
        s = s.replace({"": None, "None": None, "nan": None}, regex=False)
        s = s.astype(str)
        s = s.str.replace(".", "", regex=False)
        s = s.str.replace(",", ".", regex=False)
        chunk_df["capital_social"] = pd.to_numeric(s, errors="coerce")
    return chunk_df


def clean_estabelecimentos_chunk(chunk_df):
    date_cols = [
        "data_situacao_cadastral",
        "data_inicio_atividade",
        "data_situacao_especial",
    ]
    chunk_df = sanitize_dates(chunk_df, date_cols)
    col_name = "cnae_fiscal_secundaria"
    if col_name in chunk_df.columns:
        s = chunk_df[col_name].fillna("").astype(str)
        def to_pg_array(x):
            if not x.strip():
                return None
            parts = [p.strip() for p in re.split(r"[;,]", x) if p.strip()]
            return "{" + ",".join(parts) + "}" if parts else None
        chunk_df[col_name] = s.map(to_pg_array)
    return chunk_df


def clean_socios_chunk(chunk_df):
    return sanitize_dates(chunk_df, ["data_entrada_sociedade"])


def clean_simples_chunk(chunk_df):
    return sanitize_dates(
        chunk_df,
        [
            "data_opcao_pelo_simples",
            "data_exclusao_do_simples",
            "data_opcao_pelo_mei",
            "data_exclusao_do_mei",
        ],
    )


class ETLTableConfig(TypedDict, total=False):
    table_name: str
    column_names: list[str]
    dtype_map: Dict[str, Any]
    custom_clean_func: Callable[[pd.DataFrame], pd.DataFrame]
    critical_fields: list[str]
    primary_key_columns: list[str]


# Only tables that actually declare a key get deduplicated. `socios` has no
    # primary key in schema.sql, and a company legitimately lists the same person
    # twice: one as a partner (identificador 2) and one as a legal representative
    # (10). Treating (cnpj_basico, cnpj_cpf_socio) as a key discarded the second
    # and every later one.
ETL_CONFIG: Dict[str, ETLTableConfig] = {
    "paises": {
        "table_name": "paises",
        "column_names": ["codigo", "nome"],
        "primary_key_columns": ["codigo"],
    },
    "municipios": {
        "table_name": "municipios",
        "column_names": ["codigo", "nome"],
        "primary_key_columns": ["codigo"],
    },
    "qualificacoes": {
        "table_name": "qualificacoes_socios",
        "column_names": ["codigo", "nome"],
        "primary_key_columns": ["codigo"],
    },
    "naturezas": {
        "table_name": "naturezas_juridicas",
        "column_names": ["codigo", "nome"],
        "primary_key_columns": ["codigo"],
    },
    "cnaes": {
        "table_name": "cnaes",
        "column_names": ["codigo", "nome"],
        "primary_key_columns": ["codigo"],
    },
    "empresas": {
        "table_name": "empresas",
        "column_names": [
            "cnpj_basico",
            "razao_social",
            "natureza_juridica_codigo",
            "qualificacao_responsavel",
            "capital_social",
            "porte_empresa",
            "ente_federativo_responsavel",
        ],
        "dtype_map": {
            "cnpj_basico": pd.StringDtype(),
            "razao_social": pd.StringDtype(),
            "ente_federativo_responsavel": pd.StringDtype(),
            "natureza_juridica_codigo": pd.Int64Dtype(),
            "qualificacao_responsavel": pd.Int64Dtype(),
            "porte_empresa": pd.Int64Dtype(),
        },
        "critical_fields": ["cnpj_basico"],
        "primary_key_columns": ["cnpj_basico"],
        "custom_clean_func": clean_empresas_chunk,
    },
        "estabelecimentos": {
            "table_name": "estabelecimentos",
            "column_names": [
                "cnpj_basico",
                "cnpj_ordem",
                "cnpj_dv",
                "identificador_matriz_filial",
                "nome_fantasia",
                "situacao_cadastral",
                "data_situacao_cadastral",
                "motivo_situacao_cadastral",
                "nome_cidade_exterior",
                "pais_codigo",
                "data_inicio_atividade",
                "cnae_fiscal_principal_codigo",
                "cnae_fiscal_secundaria",
                "tipo_logradouro",
                "logradouro",
                "numero",
                "complemento",
                "bairro",
                "cep",
                "uf",
                "municipio_codigo",
                "ddd_1",
                "telefone_1",
                "ddd_2",
                "telefone_2",
                "ddd_fax",
                "fax",
                "correio_eletronico",
                "situacao_especial",
                "data_situacao_especial",
                "municipio_nome",
                "uf_source",
                "municipio_source",
            ],
            "dtype_map": {
                "cnpj_basico": pd.StringDtype(),
                "cnpj_ordem": pd.StringDtype(),
                "cnpj_dv": pd.StringDtype(),
                "identificador_matriz_filial": pd.Int64Dtype(),
                "situacao_cadastral": pd.Int64Dtype(),
                "motivo_situacao_cadastral": pd.Int64Dtype(),
                "cnae_fiscal_principal_codigo": pd.Int64Dtype(),
                "cep": pd.StringDtype(),
                "uf": pd.StringDtype(),
                "pais_codigo": pd.Int64Dtype(),
                "municipio_codigo": pd.Int64Dtype(),
                "municipio_nome": pd.StringDtype(),
                "cnae_fiscal_secundaria": pd.StringDtype(),
                "ddd_1": pd.StringDtype(),
                "telefone_1": pd.StringDtype(),
                "ddd_2": pd.StringDtype(),
                "telefone_2": pd.StringDtype(),
                "ddd_fax": pd.StringDtype(),
                "fax": pd.StringDtype(),
                "correio_eletronico": pd.StringDtype(),
                "situacao_especial": pd.StringDtype(),
                "data_situacao_cadastral": pd.StringDtype(),
                "data_inicio_atividade": pd.StringDtype(),
                "data_situacao_especial": pd.StringDtype(),
                "nome_cidade_exterior": pd.StringDtype(),
                "numero": pd.StringDtype(),
                "complemento": pd.StringDtype(),
                "tipo_logradouro": pd.StringDtype(),
                "logradouro": pd.StringDtype(),
                "uf_source": pd.StringDtype(),
                "municipio_source": pd.StringDtype(),
            },
        "critical_fields": ["cnpj_basico","cnpj_ordem","cnpj_dv"],
        "primary_key_columns": ["cnpj_basico", "cnpj_ordem", "cnpj_dv"],
        "custom_clean_func": clean_estabelecimentos_chunk,
    },
    "socios": {
        "table_name": "socios",
        "column_names": [
            "cnpj_basico",
            "identificador_socio",
            "nome_socio_ou_razao_social",
            "cnpj_cpf_socio",
            "qualificacao_socio_codigo",
            "data_entrada_sociedade",
            "pais_codigo",
            "representante_legal_cpf",
            "nome_representante_legal",
            "qualificacao_representante_legal_codigo",
            "faixa_etaria",
        ],
        "dtype_map": {
            "cnpj_basico": pd.StringDtype(),
            "identificador_socio": pd.Int64Dtype(),
            "qualificacao_socio_codigo": pd.Int64Dtype(),
            "cnpj_cpf_socio": pd.StringDtype(),
            "representante_legal_cpf": pd.StringDtype(),
            "qualificacao_representante_legal_codigo": pd.Int64Dtype(),
            "faixa_etaria": pd.Int64Dtype(),
            "pais_codigo": pd.Int64Dtype(),
            "nome_socio_ou_razao_social": pd.StringDtype(),
            "nome_representante_legal": pd.StringDtype(),
        },
        # No primary key: a company lists the same person twice on purpose
        # (partner and legal representative). cnpj_cpf_socio is nullable and
        # a foreign partner has none, so it must not gate the row either.
        "critical_fields": ["cnpj_basico"],
        "custom_clean_func": clean_socios_chunk,
    },
    "simples": {
        "table_name": "simples",
        "column_names": [
            "cnpj_basico",
            "opcao_pelo_simples",
            "data_opcao_pelo_simples",
            "data_exclusao_do_simples",
            "opcao_pelo_mei",
            "data_opcao_pelo_mei",
            "data_exclusao_do_mei",
        ],
        "dtype_map": {"cnpj_basico": pd.StringDtype()},
        "critical_fields": ["cnpj_basico"],
        "primary_key_columns": ["cnpj_basico"],
        "custom_clean_func": clean_simples_chunk,
    },
}

# --- Processador ---


def _jsonl_dir(kind: str) -> Path:
    # telemetry and quarantine are separate settings for a reason: rotation
    # cadence and size cap differ. This used to send "telemetry" to the
    # auto_repair directory, which left logs/telemetry empty forever and put the
    # per-table summary somewhere no documentation pointed at.
    base = settings.telemetry_dir if kind == "telemetry" else settings.quarantine_dir
    rotate = settings.telemetry_rotate_daily if kind == "telemetry" else settings.quarantine_rotate_daily
    if rotate:
        d = datetime.now(timezone.utc).strftime("%Y%m%d")
        return base / d
    return base


def _select_jsonl_file(dir_path: Path, base_name: str, max_bytes: int) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    base = dir_path / f"{base_name}.jsonl"
    if not base.exists() or base.stat().st_size < max_bytes:
        return base
    idx = 1
    while True:
        p = dir_path / f"{base_name}_{idx}.jsonl"
        if not p.exists() or p.stat().st_size < max_bytes:
            return p
        idx += 1


def _json_default(value):
    """Make pandas and numpy values JSON-serialisable.

    Date columns arrive as ``Timestamp`` and numeric ones as numpy scalars,
    neither of which ``json.dumps`` handles. Without this the quarantine write
    raises and the audit trail loses every affected row.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value

    # NaT is the missing-value datetime and is an instance of datetime, so it
    # has to be caught before the branch below: its isoformat() is the literal
    # string "NaT", which would make every null date in the audit trail look
    # populated.
    if value is pd.NaT:
        return None

    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()

    # numpy scalars expose .item(); NaN becomes null.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass

    # pd.isna returns an array for array-like input, and `if` on that raises,
    # which would take the whole chunk down with it.
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "tolist"):  # numpy array or pandas container
        try:
            return value.tolist()
        except Exception:
            pass
    return str(value)


def _dump_json(record) -> str:
    return json.dumps(record, ensure_ascii=False, default=_json_default)


def _write_jsonl(kind: str, config_name: str, record: dict):
    max_bytes = settings.telemetry_max_bytes if kind == "telemetry" else settings.quarantine_max_bytes
    dir_path = _jsonl_dir(kind)
    path = _select_jsonl_file(dir_path, config_name, max_bytes)
    with open(path, "a", encoding="utf-8") as f:
        f.write(_dump_json(record) + "\n")


def _chunk_records(chunk: pd.DataFrame):
    """Row dicts safe for json.dumps (NaN/NA are not valid JSON)."""
    try:
        return chunk.where(pd.notna(chunk), None).to_dict(orient="records")
    except Exception:
        logger.exception("Falha ao serializar linhas para a quarentena; nada foi recuperável.")
        return []


def _write_json(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)


def _critical_fields(config_name: str):
    cfg = ETL_CONFIG.get(config_name, {})
    return cfg.get("critical_fields", [])


# How many recent keys the duplicate filter remembers. Bounded on purpose:
# an unbounded set grows with the table, not the chunk.
_DEDUP_WINDOW = 1_000_000

BOM_BYTES = b"\xef\xbb\xbf"


def _open_volume(path: Path):
    """Open an extracted volume, skipping a UTF-8 BOM when there is one.

    Read as latin-1, the BOM becomes three characters glued to the first value,
    so the first row of every volume would carry a polluted primary key. The
    consolidation step used to strip it and was removed, so it happens here.
    Streaming rather than reading the file keeps the memory profile unchanged.
    """
    handle = open(path, "rb")
    if getattr(settings, "strip_bom", True):
        if handle.read(3) != BOM_BYTES:
            handle.seek(0)
    else:
        handle.seek(0)
    return handle


def _drop_duplicate_keys(
    chunk: pd.DataFrame,
    key_columns: list[str],
    seen: "collections.deque",
    volume: str,
) -> tuple[pd.DataFrame, int]:
    """Remove rows whose primary key was already seen, keeping the first.

    DuckDB rejects a duplicate key on insert and the transaction is per chunk, so
    without this a single repeated establishment aborts the entire load. The
    Receita publishes duplicates routinely, so they are expected rather than
    exceptional.

    Two properties this has to hold:

    * **Bounded memory.** ``seen`` used to grow with the whole table -- measured
      at roughly 16 GB for the real ``estabelecimentos`` volume -- which undid
      the streaming that makes the load viable. It is now a deque of the most
      recent keys. Duplicates in this data are contiguous, so a window catches
      them; the window size is a bound on what is remembered, not on correctness
      of what is loaded.
    * **Only rows that actually landed are remembered.** Recording a key before
      the quality gate or the critical-key filter runs meant a chunk that was
      discarded poisoned the set, and the next volume's copy of the same row was
      dropped as a duplicate of something that never reached the table.

    A row whose key contains a blank component is left alone: those are handled
    by the critical-key filter, and folding them into one "" sentinel made a
    chunk of unusable rows collapse to a single survivor.
    """
    try:
        keys = chunk[key_columns].astype("string").fillna("")
        usable = ~keys.eq("").any(axis=1)

        rows = [tuple(r) for r in keys.itertuples(index=False, name=None)]
        flags = usable.tolist()

        keep = [True] * len(rows)  # a row with a blank key is never a duplicate here
        drop_count = 0
        for i, (row, ok) in enumerate(zip(rows, flags)):
            if not ok:
                continue
            if row in seen:
                keep[i] = False
                drop_count += 1
            else:
                seen.append(row)

        if drop_count:
            logger.warning(
                "Volume %s: %d linha(s) com chave primaria repetida foram "
                "descartadas (mantendo a primeira ocorrencia).",
                volume,
                drop_count,
            )

        return chunk.loc[pd.Series(keep, index=chunk.index)], drop_count
    except Exception:
        logger.exception("Falha ao remover chaves duplicadas; o lote nao foi alterado.")
        return chunk, 0


class _VolumeReadError(Exception):
    """A volume could not be parsed, as opposed to a chunk failing to load.

    The distinction matters. A volume the reader cannot make sense of is a
    bad file: it should cost that volume and leave the others alone, and that
    is what the quarantine machinery is for. An error raised *downstream* --
    a strict FK violation, a constraint failure -- is a real problem with the
    data as a whole and has to keep aborting the run. Tagging only the read
    failures is what keeps the two apart.
    """


def _volume_chunks(
    csv_path: Path,
    etl_config: ETLTableConfig,
    key_columns: list[str],
    seen,
    stats: dict,
):
    """Yield the chunks of one volume, wrapping only reader failures.

    ``yield`` sits outside every ``try`` on purpose. A generator that yields
    inside a ``try`` also catches whatever the consumer raises at the yield
    point, and the load would sail past the failures it is supposed to abort
    on.

    ``stats`` accumulates ``rows_read`` and ``duplicate_rows_dropped`` because
    the summary has to reconcile against what the volume contained *before* the
    deduplication, not what survived it.
    """
    try:
        reader = pd.read_csv(
            _open_volume(csv_path),
            delimiter=";",
            encoding=_detect_encoding(csv_path, settings.file_encoding),
            header=None,
            names=etl_config["column_names"],
            dtype=etl_config.get("dtype_map", None),
            chunksize=settings.chunk_size,
        )
        iterator = iter(reader)
    except Exception as exc:
        raise _VolumeReadError(str(exc)) from exc

    while True:
        try:
            chunk = next(iterator)
        except StopIteration:
            return
        except Exception as exc:
            # Lazy readers surface a malformed file here, not at construction:
            # the first rows parse and a later one has the wrong width.
            raise _VolumeReadError(str(exc)) from exc

        stats["rows_read"] += int(len(chunk))
        if key_columns:
            chunk, dropped = _drop_duplicate_keys(
                chunk, key_columns, seen, csv_path.name
            )
            stats["duplicate_rows_dropped"] += dropped
            if chunk.empty:
                continue
        yield chunk


def _record_volume_failure(
    config_name: str, table_name: str, csv_path: Path, exc: BaseException
) -> None:
    """Log and quarantine a volume that could not be read.

    The record is what makes the loss recoverable: without it a skipped volume
    is indistinguishable from a volume that was never there.
    """
    logger.error(
        "Volume %s da tabela '%s' não pôde ser lido e foi pulado: %s",
        csv_path.name,
        table_name,
        exc,
    )
    _write_jsonl(
        "quarantine",
        config_name,
        {
            "table": table_name,
            "config": config_name,
            "volume": csv_path.name,
            "reason": "unreadable_volume",
            "error": f"{type(exc).__name__}: {exc}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def _native_eligible(config_name: str, etl_config: ETLTableConfig) -> bool:
    """Whether this table can be loaded without pandas at all.

    Qualifying means nothing in the pandas path would change the contents: no
    pandera schema to run, no custom cleaner and no ``dtype_map`` -- those are
    pandas extension dtypes and only mean something to the reader this path
    replaces. Everything else that the chunk loop did (dedup, column stats,
    the summary) is re-expressed in SQL instead.

    Derived rather than flagged on purpose. A table that grows a schema later
    stops qualifying by itself, instead of silently skipping validation because
    someone forgot to un-tick a box.
    """
    from .validation import SCHEMA_MAP

    return (
        config_name not in SCHEMA_MAP
        and etl_config.get("custom_clean_func") is None
        and not etl_config.get("dtype_map")
    )


def _load_table_native(
    conn: Any, config_name: str, table_name: str, volumes: list, etl_config: ETLTableConfig
) -> None:
    """Load a table end to end inside DuckDB.

    DuckDB's own reader parses the file, the relation is cast to the declared
    column types, the primary key constraint does the deduplication and a single
    INSERT writes the result. No chunk is ever materialised as a DataFrame,
    which is what the pandas path forced on a table that never needed one.

    The same summary keys are produced as the pandas path, so
    ``<config>_summary.json`` and the metrics stay comparable between the two.
    """
    columns = list(etl_config["column_names"])
    table_types = db.table_column_types(conn, table_name)
    encoding = settings.file_encoding
    strip_bom = bool(getattr(settings, "strip_bom", True))
    stage = f"_native_stage_{config_name}"

    rows_read = 0
    rows_total = 0
    duplicate_rows_dropped = 0
    failed_volumes: list = []
    column_stats: Dict[str, Any] = {}

    for csv_path in volumes:
        source_sql, params = db.csv_source_sql(
            csv_path,
            columns=columns,
            encoding=encoding,
            strip_bom=strip_bom,
            cast_types=table_types,
        )

        try:
            # Scoped to the parse on purpose. A file the reader cannot make
            # sense of costs that file; anything that goes wrong after this --
            # a constraint failure, a null key -- is a real problem with the
            # data and has to keep aborting the load.
            with db.transaction(conn):
                # The volume is parsed once and staged. Everything downstream --
                # the row count, the stats, the insert -- reads the stage, so
                # the file is never scanned twice and the stats describe exactly
                # the rows that landed rather than the ones the constraint
                # dropped.
                conn.execute(
                    f"CREATE OR REPLACE TEMP TABLE {stage} AS "
                    f"SELECT * FROM ({source_sql})",
                    params,
                )
        except Exception as exc:
            failed_volumes.append({"volume": csv_path.name, "error": str(exc)})
            _record_volume_failure(config_name, table_name, csv_path, exc)
            continue

        read = db.count_source(conn, f"SELECT * FROM {stage}")
        rows_read += read

        stats_sql, _ = db.column_stats_sql(stage, columns)
        row = conn.execute(stats_sql).fetchone()
        for idx, name in enumerate(columns):
            nulls = int(row[idx * 3] or 0)
            vmin, vmax = row[idx * 3 + 1], row[idx * 3 + 2]
            entry = column_stats.setdefault(
                name, {"nulls": 0, "min": None, "max": None}
            )
            entry["nulls"] = int(entry["nulls"]) + nulls
            if vmin is not None:
                text = str(vmin)
                if entry["min"] is None or text < str(entry["min"]):
                    entry["min"] = text
            if vmax is not None:
                text = str(vmax)
                if entry["max"] is None or text > str(entry["max"]):
                    entry["max"] = text

        cols = ", ".join(db.quote_ident(c) for c in columns)
        with db.transaction(conn):
            landed = db.insert_source(
                conn, f"SELECT {cols} FROM {stage}", [], table_name, columns
            )

        rows_total += landed
        dropped = read - landed
        duplicate_rows_dropped += dropped
        if dropped:
            logger.warning(
                "Volume %s: %d linha(s) com chave primaria repetida foram "
                "descartadas (mantendo a primeira ocorrencia).",
                csv_path.name,
                dropped,
            )

    conn.execute(f"DROP TABLE IF EXISTS {stage}")

    summary = {
        "table": table_name,
        "config": config_name,
        "rows_read": int(rows_read),
        "volumes_read": int(len(volumes)),
        "rows_total": int(rows_total),
        "rows_discarded": int(rows_read - rows_total),
        # One volume is one statement here, so the volume is the unit of work
        # rather than a fixed-size chunk.
        "chunks_processed": int(len(volumes)),
        "quality_gate_chunks": 0,
        "quality_gate_rows_discarded": 0,
        "invalid_cnpj_rows_skipped": 0,
        "critical_key_null_rows_skipped": 0,
        "duplicate_key_rows_dropped": int(duplicate_rows_dropped),
        "changed_totals": {},
        "null_delta_totals": {},
        "column_stats": column_stats,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "load_strategy": "duckdb_native",
        "volumes_failed": int(len(failed_volumes)),
        "failed_volumes": failed_volumes,
    }
    if failed_volumes:
        logger.error(
            "Tabela '%s': %d volume(s) nao puderam ser lidas e foram pulados: %s",
            table_name,
            len(failed_volumes),
            ", ".join(v["volume"] for v in failed_volumes),
        )
    _emit_summary(config_name, table_name, summary, 0, 0)
    logger.info(f"--- Tabela '{table_name}' finalizada! ---")


def process_and_load_file(conn: Any, config_name: str) -> None:
    try:
        etl_config: ETLTableConfig = ETL_CONFIG[config_name]
    except KeyError:
        logger.error(f"Configuração para '{config_name}' não encontrada.")
        return

    table_name = etl_config["table_name"]
    directory = settings.extracted_dir / config_name

    # DuckDB reads the extracted volumes in place. The consolidation step is
    # gone: no intermediate CSV, and every monthly volume is picked up directly.
    if not directory.is_dir() or not any(directory.glob("*.csv")):
        logger.warning(f"Diretório '{directory}' sem volumes CSV. Pulando.")
        return

    volumes = sorted(directory.glob("*.csv"))

    logger.info(f"--- Processando tabela '{table_name}' (via '{config_name}', {len(volumes)} volume(ns)) ---")

    if _native_eligible(config_name, etl_config):
        # Nothing here needs a DataFrame: DuckDB reads, casts, deduplicates and
        # writes. Going through pandas would only add a parse and a copy.
        logger.info(f"Tabela '{table_name}': carga nativa do DuckDB (sem pandas).")
        _load_table_native(conn, config_name, table_name, volumes, etl_config)
        return

    # The primary key is declared in CREATE TABLE and enforced on every insert.
    # The Receita data really does repeat establishments, so a duplicate has to
    # cost the duplicated rows rather than the whole load.
    # Only the declared primary key deduplicates. Falling back to
    # critical_fields would discard legitimate rows on any table whose key is
    # not enforced.
    key_columns: list[str] = [
        c
        for c in cast(list[str], etl_config.get("primary_key_columns") or [])
        if c in etl_config["column_names"]
    ]

    rows_read = 0
    volumes_read = 0
    # Volumes the reader could not parse. Kept so the summary can say the table
    # is incomplete instead of merely looking short.
    failed_volumes: list = []

    def _chunks():
        # Counts what the volumes contained, before any gate or filter. Without
        # it the summary cannot tell "the table finished" from "the table was
        # abandoned part way through".
        # duplicate_rows_dropped is bound later in this same function body
        # (line 616), which Python accepts: the compiler binds the enclosing
        # scope from the whole body, not from statement order. mypy only accepts
        # the binding before the nested def, so it is silenced here.
        nonlocal duplicate_rows_dropped, rows_read, volumes_read  # type: ignore[misc]
        seen_keys: collections.deque = collections.deque(maxlen=_DEDUP_WINDOW)
        counters: Dict[str, int] = {"rows_read": 0, "duplicate_rows_dropped": 0}
        for csv_path in volumes:
            volumes_read += 1
            try:
                yield from _volume_chunks(
                    csv_path, etl_config, key_columns, seen_keys, counters
                )
            except _VolumeReadError as exc:
                # One bad file costs that file, not the table. Anything else
                # raised downstream keeps propagating on purpose.
                failed_volumes.append({"volume": csv_path.name, "error": str(exc)})
                _record_volume_failure(config_name, table_name, csv_path, exc)
            rows_read = counters["rows_read"]
            duplicate_rows_dropped = counters["duplicate_rows_dropped"]

    total_rows = 0
    gate_level_name = getattr(settings, "gate_log_level", "WARNING")
    gate_level = logging.WARNING if str(gate_level_name).upper() == "WARNING" else logging.INFO
    changed_totals: Dict[str, int] = {}
    null_delta_totals: Dict[str, int] = {}
    quality_gate_chunks = 0
    quality_gate_rows = 0
    critical_null_rows = 0
    duplicate_rows_dropped = 0
    invalid_cnpj_rows = 0
    chunks_processed = 0
    class ColumnStats(TypedDict):
        nulls: int
        min: Optional[str]
        max: Optional[str]

    column_stats: Dict[str, ColumnStats] = {}
    for i, chunk in enumerate(_chunks()):
        chunk_start = datetime.now(timezone.utc)
        clean = etl_config.get("custom_clean_func")
        if clean is not None:
            chunk = clean(chunk)
        chunk, telemetry, masks = schema_validate(config_name, chunk)
        rows_count = int(len(chunk))
        telemetry_record = {
            "table": table_name,
            "config": config_name,
            "chunk": i + 1,
            "rows": rows_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((datetime.now(timezone.utc) - chunk_start).total_seconds() * 1000),
            **telemetry,
        }
        if getattr(settings, "enable_quality_gates", True) and rows_count >= getattr(settings, "gate_min_rows", 20):
            triggers = {}
            changed = telemetry.get("changed_counts", {}) or {}
            deltas = telemetry.get("null_deltas", {}) or {}
            for c, n in changed.items():
                try:
                    ratio = (int(n) / rows_count)
                except Exception:
                    ratio = 0.0
                if ratio > getattr(settings, "gate_max_changed_ratio", 0.3):
                    triggers[c] = {"type": "changed", "ratio": ratio, "count": int(n)}
            for c, d in deltas.items():
                try:
                    ratio = (abs(int(d)) / rows_count)
                except Exception:
                    ratio = 0.0
                if ratio > getattr(settings, "gate_max_null_delta_ratio", 0.3):
                    if c in triggers:
                        triggers[c]["null_delta_ratio"] = ratio
                        triggers[c]["null_delta"] = int(d)
                    else:
                        triggers[c] = {"type": "null_delta", "ratio": ratio, "delta": int(d)}
            if triggers:
                now = datetime.now(timezone.utc).isoformat()
                telemetry_record["quality_gate"] = {"trigger_columns": triggers, "rows": rows_count}
                _write_jsonl("telemetry", config_name, telemetry_record)
                _write_jsonl(
                    "quarantine",
                    config_name,
                    {
                        "table": table_name,
                        "config": config_name,
                        "chunk": i + 1,
                        "reason": "quality_gate",
                        "trigger_columns": triggers,
                        "rows": rows_count,
                        "timestamp": now,
                    },
                )
                # The chunk is dropped from the load, so the rows themselves have to
                # be kept: an aggregate record alone is not enough to recover the
                # data or to account for the gap.
                for rec in _chunk_records(chunk):
                    _write_jsonl(
                        "quarantine",
                        config_name,
                        {
                            "table": table_name,
                            "config": config_name,
                            "chunk": i + 1,
                            "reason": "quality_gate",
                            "row": rec,
                            "timestamp": now,
                        },
                    )
                quality_gate_chunks += 1
                quality_gate_rows += rows_count
                logger.log(
                    gate_level,
                    f"Chunk {i + 1}: quality gate descartou {rows_count} linhas "
                    f"(colunas: {sorted(triggers)}). Recuperáveis em "
                    f"{_jsonl_dir('quarantine')}.",
                )
                continue
        else:
            _write_jsonl("telemetry", config_name, telemetry_record)

        for c, n in (telemetry.get("changed_counts", {}) or {}).items():
            changed_totals[c] = changed_totals.get(c, 0) + int(n)
        for c, d in (telemetry.get("null_deltas", {}) or {}).items():
            try:
                null_delta_totals[c] = null_delta_totals.get(c, 0) + int(d)
            except Exception:
                pass

        crit = _critical_fields(config_name)
        if crit:
            cols = [c for c in crit if c in chunk.columns]
            if cols:
                mask = chunk[cols].isna().any(axis=1)
                if mask.any():
                    bad = chunk.loc[mask].copy()
                    try:
                        bad = bad.where(pd.notna(bad), None)
                        for rec in bad.to_dict(orient="records"):
                            _write_jsonl(
                                "quarantine",
                                config_name,
                                {
                                    "table": table_name,
                                    "config": config_name,
                                    "chunk": i + 1,
                                    "reason": "critical_fields_null",
                                    "fields": cols,
                                    "row": rec,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                    except Exception:
                        # Dropping a row without its record in quarantine would
                        # lose it silently, so the load stops instead.
                        logger.exception(
                            "Falha ao registrar chave nula em quarentena; "
                            "o lote nao sera alterado."
                        )
                        raise
                    # A row without its primary key cannot be stored: the
                    # constraint is part of CREATE TABLE, so leaving it in the
                    # chunk would abort the whole load rather than skip a row.
                    chunk = chunk.loc[~mask]
                    critical_null_rows += int(mask.sum())
                    if chunk.empty:
                        logger.warning(
                            f"Chunk {i + 1}: todas as linhas tinham chave primaria nula; nada a carregar."
                        )
                        # continue, not break: a single all-null chunk must not
                        # abandon every remaining chunk of this volume and every
                        # volume after it.
                        continue
        if config_name == "estabelecimentos" and isinstance(masks, dict) and masks.get("invalid_cnpj") is not None:
            try:
                bad = chunk.loc[masks["invalid_cnpj"]].copy()
                bad = bad.where(pd.notna(bad), None)
                for rec in bad.to_dict(orient="records"):
                    _write_jsonl(
                        "quarantine",
                        config_name,
                        {
                            "table": table_name,
                            "config": config_name,
                            "chunk": i + 1,
                            "reason": "invalid_cnpj",
                            "fields": ["cnpj_basico","cnpj_ordem","cnpj_dv"],
                            "row": rec,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )
            except Exception:
                logger.exception(
                    "Falha ao registrar CNPJ invalido em quarentena; "
                    "as linhas nao serao removidas."
                )
                raise
            if getattr(settings, "skip_invalid_estabelecimentos_cnpj", False):
                skip_count = int(masks["invalid_cnpj"].sum())
                if skip_count:
                    chunk = chunk.loc[~masks["invalid_cnpj"]].copy()
                    logger.info(f"Chunk {i + 1}: skipped {skip_count} estabelecimentos rows due to invalid CNPJ.")
                    invalid_cnpj_rows += skip_count
                    if len(chunk) == 0:
                        logger.info(f"Chunk {i + 1}: no rows left after skip; continuing.")
                        continue
        try:
            fk_info = validate_chunk(config_name, chunk, conn)
        except ValueError as e:
            raise ValueError(f"{str(e)} | telemetry_after={json.dumps(telemetry, ensure_ascii=False)}")
        if isinstance(fk_info, dict) and fk_info:
            for label, mask in fk_info.items():
                try:
                    bad = chunk.loc[mask].copy()
                    bad = bad.where(pd.notna(bad), None)
                    for rec in bad.to_dict(orient="records"):
                        _write_jsonl(
                            "quarantine",
                            config_name,
                            {
                                "table": table_name,
                                "config": config_name,
                                "chunk": i + 1,
                                "reason": "fk_violation",
                                "fields": [label],
                                "row": rec,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                except Exception:
                    # A failure here means the offending rows vanish from the
                    # audit trail without a trace, so it is logged rather than
                    # swallowed.
                    logger.exception(
                        "Falha ao registrar FK inválida em quarentena "
                        "(chave %s, %d linha(s) afetada(s)).",
                        label,
                        int(mask.sum()),
                    )

        # Passa a conexão direta
        fast_load_chunk(conn, chunk, table_name)

        for col in chunk.columns:
            s = chunk[col]
            nulls = int(s.isna().sum())
            cs = column_stats.get(col)
            if cs is None:
                cs = {"nulls": 0, "min": None, "max": None}
                column_stats[col] = cs
            cs["nulls"] = int(cs["nulls"]) + nulls
            vmin = None
            vmax = None
            try:
                vmin = s.dropna().min()
                vmax = s.dropna().max()
            except Exception:
                vmin = None
                vmax = None
            if vmin is not None:
                vmin_str = str(vmin)
                if cs["min"] is None or vmin_str < str(cs["min"]):
                    cs["min"] = vmin_str
            if vmax is not None:
                vmax_str = str(vmax)
                if cs["max"] is None or vmax_str > str(cs["max"]):
                    cs["max"] = vmax_str

        total_rows += len(chunk)
        chunks_processed += 1
        logger.info(f"  ... Chunk {i + 1} processado. Total: {total_rows} linhas.")

    summary = {
        "table": table_name,
        "config": config_name,
        "rows_read": int(rows_read),
        "volumes_read": int(volumes_read),
        "rows_total": int(total_rows),
        "rows_discarded": int(rows_read - total_rows),
        "chunks_processed": int(chunks_processed),
        "quality_gate_chunks": int(quality_gate_chunks),
        "quality_gate_rows_discarded": int(quality_gate_rows),
        "invalid_cnpj_rows_skipped": int(invalid_cnpj_rows),
        "critical_key_null_rows_skipped": int(critical_null_rows),
        "duplicate_key_rows_dropped": int(duplicate_rows_dropped),
        "changed_totals": changed_totals,
        "null_delta_totals": null_delta_totals,
        "column_stats": column_stats,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "load_strategy": "pandas_chunked",
        "volumes_failed": int(len(failed_volumes)),
        "failed_volumes": failed_volumes,
    }
    _emit_summary(config_name, table_name, summary, quality_gate_rows, quality_gate_chunks)
    logger.info(f"--- Tabela '{table_name}' finalizada! ---")


def _emit_summary(
    config_name: str, table_name: str, summary: dict, quality_gate_rows: int, quality_gate_chunks: int
) -> None:
    """Persist the per-table summary and push the optional metrics.

    Shared by both load strategies so ``<config>_summary.json`` means the same
    thing whichever path produced it.
    """
    total_rows = summary["rows_total"]
    changed_totals = summary["changed_totals"]
    null_delta_totals = summary["null_delta_totals"]
    dir_path = _jsonl_dir("telemetry")
    _write_json(dir_path / f"{config_name}_summary.json", summary)
    if getattr(settings, "enable_metrics_prometheus", False):
        metrics_lines = []
        metrics_lines.append(f"cnpj_auto_repair_rows_total{{table=\"{table_name}\"}} {int(total_rows)}")
        metrics_lines.append(f"cnpj_auto_repair_quality_gate_chunks_total{{table=\"{table_name}\"}} {int(quality_gate_chunks)}")
        for c, n in changed_totals.items():
            metrics_lines.append(f"cnpj_auto_repair_changed_total{{table=\"{table_name}\",column=\"{c}\"}} {int(n)}")
        for c, d in null_delta_totals.items():
            metrics_lines.append(f"cnpj_auto_repair_null_delta_total{{table=\"{table_name}\",column=\"{c}\"}} {int(d)}")
        p = getattr(settings, "prometheus_metrics_path", None)
        out_path = Path(p) if p else (dir_path / "metrics.prom")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write("\n".join(metrics_lines) + "\n")
        if getattr(settings, "enable_prometheus_push", False) and getattr(settings, "prometheus_push_url", None):
            job = getattr(settings, "prometheus_job", "cnpj_auto_repair")
            inst = getattr(settings, "prometheus_instance", "local")
            url = str(getattr(settings, "prometheus_push_url"))
            push_url = url.rstrip("/") + f"/metrics/job/{job}/instance/{inst}"
            try:
                requests.post(push_url, data="\n".join(metrics_lines) + "\n", headers={"Content-Type": "text/plain"}, timeout=10)
            except Exception:
                pass
    if getattr(settings, "enable_otlp_push", False) and getattr(settings, "otlp_endpoint", None):
        try:
            requests.post(str(getattr(settings, "otlp_endpoint")), json=summary, timeout=10)
        except Exception:
            pass
    if quality_gate_rows:
        logger.warning(
            f"Tabela '{table_name}': {quality_gate_rows} linhas em "
            f"{quality_gate_chunks} chunk(s) NÃO foram carregadas por quality gate. "
            f"Estão em {_jsonl_dir('quarantine')} — reexecute com "
            f"ENABLE_QUALITY_GATES=false para carregá-las."
        )


# --- Executor de SQL ---


def execute_sql_file(conn, filename, drop_tables=None):
    """
    Lê e executa um arquivo SQL na conexão DuckDB.
    """
    base_path = Path(__file__).parent
    file_path = base_path / filename

    if not file_path.exists():
        logger.error(f"Arquivo SQL não encontrado: {file_path}")
        return

    logger.info(f"Executando SQL: {filename}")
    with open(file_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    if filename == "schema.sql" and getattr(settings, "allow_drop", False):
        # O DROP e decidido aqui em Python, porque quem sabe o que esta carga
        # vai tocar e o loader -- e nao o SQL.
        # Only the tables this run is about to load are dropped: a --only run
        # must not empty the tables it was not asked to touch.
        targets = drop_tables if drop_tables is not None else None
        sql_content = _drop_statements(targets) + "\n" + sql_content

    try:
        db.apply_script(conn, sql_content)
        logger.info(f"Sucesso ao executar {filename}")
    except Exception as e:
        logger.error(f"ERRO ao executar SQL de {filename}: {e}")
        raise


# Tables whose foreign keys point at each table, mirroring schema.sql.
_FK_REFERENCES: Dict[str, set] = {
    "paises": {"estabelecimentos", "socios"},
    "municipios": {"estabelecimentos"},
    "cnaes": {"estabelecimentos"},
    "naturezas_juridicas": {"empresas"},
    "qualificacoes_socios": {"empresas", "socios"},
    "empresas": {"estabelecimentos", "simples", "socios"},
    "estabelecimentos": set(),
    "simples": set(),
    "socios": set(),
}


def _dependent_closure(tables: list) -> set:
    """``tables`` plus everything whose foreign keys point at them.

    DuckDB will not drop a table that another table references, so reloading a
    table implies dropping its dependents. Making that explicit lets the loader
    refuse a partial reload it cannot honour instead of emptying tables it will
    not refill.
    """
    closure = set(tables)
    changed = True
    while changed:
        changed = False
        for parent, dependents in _FK_REFERENCES.items():
            if parent in closure and not dependents <= closure:
                closure |= dependents
                changed = True
    return closure


# Drop order matters: children before parents, because they reference them.
_DROP_ORDER = [
    "socios",
    "simples",
    "estabelecimentos",
    "empresas",
    "cnaes",
    "naturezas_juridicas",
    "qualificacoes_socios",
    "municipios",
    "paises",
]


def _drop_statements(tables=None) -> str:
    """Drops for ``tables``, or every project table when it is None."""
    wanted = set(tables) if tables is not None else set(_DROP_ORDER)
    ordered = [t for t in _DROP_ORDER if t in wanted]
    return "\n".join(
        f"DROP TABLE IF EXISTS {db.quote_ident(t)} CASCADE;" for t in ordered
    )


def execute_sql_path(conn, path: Path):
    if not path.exists():
        logger.error(f"Arquivo SQL não encontrado: {path}")
        return
    logger.info(f"Executando SQL: {path.name}")
    sql_content = path.read_text(encoding="utf-8")
    try:
        db.apply_script(conn, sql_content)
        logger.info(f"Sucesso ao executar {path.name}")
    except Exception as e:
        logger.error(f"ERRO ao executar SQL de {path.name}: {e}")
        raise


def run_queries_in_dir(dir_path: Path):
    if not dir_path.is_dir():
        logger.info(f"Diretório de queries '{dir_path}' não existe; nada a executar.")
        return
    files = sorted(dir_path.glob("*.sql"))
    if not files:
        logger.info("Nenhuma query customizada encontrada.")
        return
    conn = None
    try:
        conn = db.connect(settings.duckdb_file)
        for f in files:
            execute_sql_path(conn, f)
        logger.info("Execução de queries finalizada.")
    except Exception as e:
        logger.error(f"Erro durante execução de queries: {e}")
    finally:
        if conn is not None:
            conn.close()


# --- Orquestrador Principal ---


def _volumes_available(config_names: list[str]) -> list[str]:
    """Configs that have no extracted volume on disk."""
    missing = []
    base = settings.extracted_dir
    for name in config_names:
        directory = base / name
        if not directory.is_dir():
            missing.append(name)
            continue
        volumes = list(directory.glob("*.csv"))
        # A zero-byte volume is a truncated download, not input. Counting it as
        # available let the schema be dropped and the table silently emptied.
        if not volumes or all(v.stat().st_size == 0 for v in volumes):
            missing.append(name)
    return missing


def db_count_populated(path: Path) -> int:
    """Project tables that actually hold rows.

    Counting tables is not enough: a database left behind by a run that failed
    after the schema was created has all the tables and no data, and refusing to
    reload it would strand the user with an unusable file.
    """
    try:
        if not Path(path).exists():
            return 0
    except OSError:
        return 0
    try:
        probe = db.connect(path, read_only=True)
    except Exception:
        return 0
    try:
        tables = [
            r[0]
            for r in probe.execute(
                "SELECT table_name FROM duckdb_tables() "
                "WHERE schema_name = 'main' AND internal = false"
            ).fetchall()
        ]
        populated = 0
        for table in tables:
            try:
                if db.count_rows(probe, table) > 0:
                    populated += 1
            except Exception:
                continue
        return populated
    except Exception:
        return 0
    finally:
        probe.close()


def run_loader(only=None, exclude=None):
    logger.info("Iniciando carga para DuckDB...")

    # Parents before children: DuckDB enforces foreign keys during the insert,
    # so a child cannot land before its parent table exists.
    domain_order = ["paises", "municipios", "qualificacoes", "naturezas", "cnaes"]
    child_order = ["empresas", "estabelecimentos", "simples", "socios"]

    if only:
        domain_order = [x for x in domain_order if x in set(only)]
        child_order = [x for x in child_order if x in set(only)]
    if exclude:
        domain_order = [x for x in domain_order if x not in set(exclude)]
        child_order = [x for x in child_order if x not in set(exclude)]

    # The schema is dropped before a single row is read, so a run that turns out to
    # be missing input leaves an empty database behind. Refuse only when there is
    # data at risk: an extract that simply has no volumes for some tables is
    # normal for a given month, and dropping an empty database costs nothing.
    planned = domain_order + child_order
    missing = _volumes_available(planned)
    if missing:
        existing = db_count_populated(settings.duckdb_file)
        if existing:
            logger.error(
                "Sem volumes extraidos para: %s, e o banco ja tem %d tabela(s) "
                "com dados. Nada foi apagado; o banco permanece intacto. Rode "
                "`python -m src --step extract` antes de recarregar.",
                ", ".join(missing),
                existing,
            )
            raise FileNotFoundError(f"volumes ausentes: {', '.join(missing)}")
        logger.warning(
            "Sem volumes extraidos para: %s. Esses passos serão ignorados.",
            ", ".join(missing),
        )

    if getattr(settings, "allow_drop", False) and planned:
        logger.info("ALLOW_DROP=true: recriando o schema antes da carga.")
    else:
        existing = db_count_populated(settings.duckdb_file)
        if existing:
            # Reusing populated tables is not implementable: the primary keys
            # reject every second insert. Saying "tables will be reused" and
            # then aborting on the first duplicate key is the worst of both.
            raise RuntimeError(
                f"O banco em {settings.duckdb_file} ja tem {existing} tabela(s) "
                "com dados e ALLOW_DROP=false, entao uma recarga colidiria com as "
                "chaves existentes. Defina ALLOW_DROP=true para recriar do zero, "
                "ou apague o arquivo para comecar limpo."
            )

    tables_to_load = [
        ETL_CONFIG[c]["table_name"] for c in planned if c in ETL_CONFIG
    ]
    tables_to_drop = sorted(_dependent_closure(tables_to_load))
    outside = sorted(set(tables_to_drop) - set(tables_to_load))
    if outside and db_count_populated(settings.duckdb_file):
        # DuckDB will not drop a table another table's foreign key points at, so
        # reloading one table means emptying everything that references it.
        # Honouring that silently would lose data this run will not refill.
        # Checked before opening the file: db_count_populated needs it read-only
        # and DuckDB refuses a second configuration on an open database.
        raise RuntimeError(
            "Recarga parcial sobre um banco populado e impossivel: recarregar "
            f"{sorted(tables_to_load)} exigiria descartar tambem {outside}, "
            "que esta execucao nao vai repovoar. Use ALLOW_DROP=true sem "
            "--only/--exclude para recriar tudo, ou --only em um banco vazio."
        )

    try:
        conn = db.connect(settings.duckdb_file)
    except Exception as e:
        logger.error(f"Erro ao abrir o banco: {e}")
        # Propagate: a silent return made the orchestrator mark the load as
        # completed after hours of downloading, with nothing in the database.
        raise

    try:
        clear_domain_cache()
        execute_sql_file(conn, "schema.sql", drop_tables=tables_to_drop)

        for config_name in domain_order:
            process_and_load_file(conn, config_name)

        # Two backfill phases, because the rules cannot all run at once. The
        # domain lookups are referenced by empresas, so they are filled before it
        # loads; the synthetic companies are referenced by the tables that load
        # after it, so they can only be identified once empresas is populated.
        backfill_on = getattr(settings, "enable_constraints_backfill", True)
        if not backfill_on:
            logger.info("Backfill de dominios desativado (ENABLE_CONSTRAINTS_BACKFILL=false).")

        if backfill_on:
            # The two phases read the same volumes, so the first keeps the scan
            # and the second reuses it rather than reading them twice.
            #
            # The domain lookups are referenced by empresas, so they are filled
            # before it loads.
            backfill.run_backfill(
                conn, parents=backfill.DOMAIN_PARENTS, keep_cache=True
            )

        # empresas is a child like any other and must honour --only/--exclude.
        # Loading it unconditionally emptied the filtered-out tables and, because
        # no domain volume was read, relabelled every lookup as synthetic.
        loads_empresas = "empresas" in child_order
        if loads_empresas:
            process_and_load_file(conn, "empresas")

        if backfill_on and loads_empresas:
            # Synthetic companies are referenced by the tables that load after
            # empresas, so they can only be identified once it is populated.
            backfill.run_backfill(conn, parents=backfill.EMPRESA_PARENTS)

        for config_name in child_order:
            if config_name == "empresas":
                continue
            process_and_load_file(conn, config_name)

        conn.execute("CHECKPOINT")
        logger.info("Carga finalizada com sucesso.")

    except Exception as e:
        logger.error(f"Erro crítico durante o processo: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    from .settings import setup_logging

    setup_logging()
    run_loader()
def _detect_encoding(file_path: Path, default: str) -> str:
    try:
        from charset_normalizer import from_path
        res = from_path(str(file_path)).best()
        if res and res.encoding:
            return res.encoding
    except Exception:
        return default
    return default
def clear_domain_cache():
    DOMAIN_CACHE.clear()


def run_constraints():
    """Recompute statistics and flush the write-ahead log.

    DuckDB creates every constraint and index as part of ``schema.sql`` and
    keeps statistics itself, so there is nothing left to apply here: what is
    left is the ANALYZE and the CHECKPOINT.
    """
    logger.info("Otimizando e consolidando o banco...")
    conn = None
    try:
        conn = db.connect(settings.duckdb_file)
        conn.execute("CHECKPOINT")
        for table in (
            "empresas",
            "estabelecimentos",
            "socios",
            "simples",
            "paises",
            "municipios",
            "qualificacoes_socios",
            "naturezas_juridicas",
            "cnaes",
        ):
            if db.table_exists(conn, table):
                conn.execute(f"ANALYZE {db.quote_ident(table)}")
        conn.execute("CHECKPOINT")
        logger.info("Otimizacao concluida.")
    except Exception as e:
        logger.error(f"Erro ao otimizar o banco: {e}")
        raise
    finally:
        if conn is not None:
            conn.close()
FK_RULES: Dict[str, list[tuple[str, str | None, str | None]]] = {
    "empresas": [
        ("natureza_juridica_codigo","naturezas_juridicas","codigo"),
        ("qualificacao_responsavel","qualificacoes_socios","codigo"),
    ],
    "estabelecimentos": [
        ("pais_codigo","paises","codigo"),
        ("municipio_codigo","municipios","codigo"),
        ("cnae_fiscal_principal_codigo","cnaes","codigo"),
        ("uf", None, None),
        # Removed "empresas" from python-side validation to save memory
    ],
    "socios": [
        ("pais_codigo","paises","codigo"),
        ("qualificacao_socio_codigo","qualificacoes_socios","codigo"),
        ("qualificacao_representante_legal_codigo","qualificacoes_socios","codigo"),
        # Removed "empresas" from python-side validation to save memory
    ],
}

DOMAINS_REQUIRED: Dict[str, list[str]] = {
    "empresas": ["naturezas_juridicas","qualificacoes_socios"],
    "estabelecimentos": ["paises","municipios","cnaes"], # Removed "empresas"
    "socios": ["paises","qualificacoes_socios"], # Removed "empresas"
}
