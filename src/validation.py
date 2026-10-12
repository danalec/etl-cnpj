import pandas as pd
import re
import logging
import unicodedata
from typing import TypeVar, Generic, ClassVar, Any, Optional, Tuple
from pathlib import Path
from .settings import settings, UF_SET

# Sem anotacao o mypy infere o tipo a partir da atribuicao inicial e conclui que
# a guarda "is not None" nunca passa, ou seja, que nenhum codigo aqui seria
# alcancavel.
CEP_MAP_CACHE: Optional[Tuple[str, pd.DataFrame]] = None
MUN_NAME_MAP_CACHE: Optional[Tuple[str, pd.DataFrame]] = None

def _cpf_valid(s: str) -> bool:
    if not s or len(s) != 11 or s == s[0] * 11:
        return False
    nums = [int(x) for x in s]
    sm1 = sum(nums[i] * (10 - i) for i in range(9))
    d1 = 0 if sm1 % 11 < 2 else 11 - (sm1 % 11)
    if nums[9] != d1:
        return False
    sm2 = sum(nums[i] * (11 - i) for i in range(10))
    d2 = 0 if sm2 % 11 < 2 else 11 - (sm2 % 11)
    return nums[10] == d2

def _cnpj_valid(s: str) -> bool:
    if not s or len(s) != 14 or s == s[0] * 14:
        return False
    nums = [int(x) for x in s]
    w1 = [5,4,3,2,9,8,7,6,5,4,3,2]
    sm1 = sum(nums[i] * w1[i] for i in range(12))
    d1 = 0 if sm1 % 11 < 2 else 11 - (sm1 % 11)
    if nums[12] != d1:
        return False
    w2 = [6,5,4,3,2,9,8,7,6,5,4,3,2]
    sm2 = sum(nums[i] * w2[i] for i in range(13))
    d2 = 0 if sm2 % 11 < 2 else 11 - (sm2 % 11)
    return nums[13] == d2

def _digits_only(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\D+", "", regex=True)

def _strip_empty_to_na(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.strip()
    return t.mask(t.eq(""))

def _ensure_len(s: pd.Series, length: int) -> pd.Series:
    d = _digits_only(s)
    return d.where(d.str.len().eq(length))

def _normalize_uf(s: pd.Series) -> pd.Series:
    val = s.astype(str).str.strip().str.upper()
    return val.where(val.isin(UF_SET))

def _normalize_email(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.strip().str.lower()
    return t.where(t.str.contains(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", regex=True))

def _normalize_email_strict(s: pd.Series) -> pd.Series:
    def norm(x: str | None) -> str | None:
        if x is None:
            return None
        t = str(x).strip()
        if not t or t.count("@") != 1:
            return None
        local, domain = t.split("@", 1)
        if not local or not domain:
            return None
        if local.startswith(".") or local.endswith(".") or ".." in local:
            return None
        if not re.fullmatch(r"[A-Za-z0-9._%+-]+", local):
            return None
        d = domain.strip().lower().rstrip(".")
        labels = d.split(".")
        if len(labels) < 2:
            return None
        for lbl in labels:
            if not re.fullmatch(r"[a-z0-9-]+", lbl):
                return None
            if lbl.startswith("-") or lbl.endswith("-") or len(lbl) == 0 or len(lbl) > 63:
                return None
        if len(labels[-1]) < 2:
            return None
        return local + "@" + ".".join(labels)
    return s.map(norm)

def _normalize_cnae_code(s: pd.Series) -> pd.Series:
    d = _digits_only(s)
    return pd.to_numeric(d.where(d.str.len().eq(7)), errors="coerce").astype("Int64")

def _normalize_pg_array_digits(s: pd.Series) -> pd.Series:
    def fix(x: str | None) -> str | None:
        if x is None:
            return None
        xs = str(x).strip()
        if xs == "":
            return None
        if xs.startswith("{") and xs.endswith("}"):
            body = xs[1:-1]
            tokens = [p.strip() for p in body.split(",")]
        else:
            tokens = [p.strip() for p in re.split(r"[;,]", xs) if p.strip()]
        digits = [re.sub(r"\D+", "", t) for t in tokens]
        clean = [d for d in digits if len(d) == 7]
        return ("{" + ",".join(clean) + "}") if clean else None
    return s.map(fix)

def _dedup_sort_pg_array(s: pd.Series) -> pd.Series:
    def fix(x: str | None) -> str | None:
        if x is None:
            return None
        xs = str(x).strip()
        if not xs or not xs.startswith("{") or not xs.endswith("}"):
            return None
        body = xs[1:-1]
        toks = [t for t in body.split(",") if t]
        try:
            nums = sorted({int(t) for t in toks})
        except Exception:
            return None
        return ("{" + ",".join(str(n) for n in nums) + "}") if nums else None
    return s.map(fix)

def _get_cep_map():
    global CEP_MAP_CACHE
    p = getattr(settings, "cep_map_path", None)
    if not p:
        return None
    # The cache is keyed on the path. A bare "cached or not" flag meant that
    # pointing CEP_MAP_PATH at a different file kept feeding the old map to
    # every remaining chunk of the run.
    if CEP_MAP_CACHE is not None and CEP_MAP_CACHE[0] == str(p):
        return CEP_MAP_CACHE[1]
    try:
        path = Path(p)
        if not path.exists():
            return None
        # dtype=str: an all-digit CEP column would otherwise parse as int64 and
        # the merge against the normalized keys would raise.
        dfm = pd.read_csv(path, dtype=str)
        cols = set(dfm.columns)
        required_any = [{"cep","uf","municipio_codigo"}, {"cep_prefix","uf","municipio_codigo"}]
        if any(s.issubset(cols) for s in required_any):
            CEP_MAP_CACHE = (str(p), dfm)
            return dfm
    except Exception:
        return None
    return None


def _get_mun_name_map():
    global MUN_NAME_MAP_CACHE
    p = getattr(settings, "municipio_name_map_path", None)
    if not p:
        return None
    if MUN_NAME_MAP_CACHE is not None and MUN_NAME_MAP_CACHE[0] == str(p):
        return MUN_NAME_MAP_CACHE[1]
    try:
        path = Path(p)
        if not path.exists():
            return None
        # dtype=str so the nome comparison does not depend on how the CSV
        # happened to be typed.
        dfm = pd.read_csv(path, dtype=str)
        cols = set(dfm.columns)
        if {"municipio_nome","municipio_codigo"}.issubset(cols):
            MUN_NAME_MAP_CACHE = (str(p), dfm)
            return dfm
    except Exception:
        return None
    return None

def _normalize_name_series(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.strip().str.lower()
    def deacc(x: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKD", x) if not unicodedata.combining(c))
    return t.map(deacc)

T = TypeVar("T")
class PanderaSeries(Generic[T]):
    pass

class SchemaModel:
    @classmethod
    def validate(cls, df, lazy=True):
        return df
    DIFF_COLUMNS: ClassVar[list[str]] = []

    @classmethod
    def clean(cls, df: pd.DataFrame, level: str, settings_obj, helpers) -> tuple[pd.DataFrame, dict[str, pd.Series], dict[str, Any]]:
        return df, {}, {}


class EmpresasSchema(SchemaModel):
        cnpj_basico: PanderaSeries[str]
        razao_social: PanderaSeries[str]
        natureza_juridica_codigo: PanderaSeries[pd.Int64Dtype]
        qualificacao_responsavel: PanderaSeries[pd.Int64Dtype]
        capital_social: PanderaSeries[float]
        porte_empresa: PanderaSeries[pd.Int64Dtype]
        ente_federativo_responsavel: PanderaSeries[str]
        

        @classmethod
        def clean(cls, df: pd.DataFrame, level: str, settings_obj, helpers) -> tuple[pd.DataFrame, dict[str, pd.Series], dict[str, Any]]:
                if "cnpj_basico" in df.columns:
                        df["cnpj_basico"] = helpers["ensure_len"](df["cnpj_basico"], 8)
                if "razao_social" in df.columns:
                        df["razao_social"] = helpers["strip_empty_to_na"](df["razao_social"])
                if "ente_federativo_responsavel" in df.columns:
                        df["ente_federativo_responsavel"] = helpers["strip_empty_to_na"](df["ente_federativo_responsavel"])
                return df, {}, {}


class EstabelecimentosSchema(SchemaModel):
        cnpj_basico: PanderaSeries[str]
        cnpj_ordem: PanderaSeries[str]
        cnpj_dv: PanderaSeries[str]
        identificador_matriz_filial: PanderaSeries[pd.Int64Dtype]
        nome_fantasia: PanderaSeries[str]
        situacao_cadastral: PanderaSeries[pd.Int64Dtype]
        data_situacao_cadastral: PanderaSeries[object]
        motivo_situacao_cadastral: PanderaSeries[pd.Int64Dtype]
        nome_cidade_exterior: PanderaSeries[str]
        pais_codigo: PanderaSeries[pd.Int64Dtype]
        data_inicio_atividade: PanderaSeries[object]
        cnae_fiscal_principal_codigo: PanderaSeries[pd.Int64Dtype]
        cnae_fiscal_secundaria: PanderaSeries[str]
        tipo_logradouro: PanderaSeries[str]
        logradouro: PanderaSeries[str]
        numero: PanderaSeries[str]
        complemento: PanderaSeries[str]
        bairro: PanderaSeries[str]
        cep: PanderaSeries[str]
        uf: PanderaSeries[str]
        municipio_codigo: PanderaSeries[pd.Int64Dtype]
        ddd_1: PanderaSeries[str]
        telefone_1: PanderaSeries[str]
        ddd_2: PanderaSeries[str]
        telefone_2: PanderaSeries[str]
        ddd_fax: PanderaSeries[str]
        fax: PanderaSeries[str]
        correio_eletronico: PanderaSeries[str]
        situacao_especial: PanderaSeries[str]
        data_situacao_especial: PanderaSeries[object]
        DIFF_COLUMNS: ClassVar[list[str]] = [
            "cnpj_basico","cnpj_ordem","cnpj_dv","uf","cep",
            "cnae_fiscal_principal_codigo","cnae_fiscal_secundaria","correio_eletronico"
        ]


        @classmethod
        def clean(cls, df: pd.DataFrame, level: str, settings_obj, helpers) -> tuple[pd.DataFrame, dict[str, pd.Series], dict[str, Any]]:
                masks: dict[str, pd.Series] = {}
                extras: dict[str, Any] = {}
                cep_enrichment: dict[str, Any] = {}
                if "cnpj_basico" in df.columns:
                    df["cnpj_basico"] = helpers["ensure_len"](df["cnpj_basico"], 8)
                if "cnpj_ordem" in df.columns:
                    df["cnpj_ordem"] = helpers["ensure_len"](df["cnpj_ordem"], 4)
                if "cnpj_dv" in df.columns:
                    df["cnpj_dv"] = helpers["ensure_len"](df["cnpj_dv"], 2)
                for c in [
                    "nome_fantasia","tipo_logradouro","logradouro","complemento","bairro",
                    "correio_eletronico","situacao_especial","nome_cidade_exterior","municipio_nome"
                ]:
                    if c in df.columns:
                        df[c] = helpers["strip_empty_to_na"](df[c])
                if "correio_eletronico" in df.columns:
                    df["correio_eletronico"] = helpers["normalize_email"](df["correio_eletronico"])
                if "cep" in df.columns:
                    df["cep"] = helpers["ensure_len"](df["cep"], 8)
                if "uf" in df.columns:
                    df["uf"] = helpers["normalize_uf"](df["uf"])
                if "cnae_fiscal_principal_codigo" in df.columns:
                    df["cnae_fiscal_principal_codigo"] = helpers["normalize_cnae_code"](df["cnae_fiscal_principal_codigo"])
                if "cnae_fiscal_secundaria" in df.columns:
                    df["cnae_fiscal_secundaria"] = helpers["normalize_pg_array_digits"](df["cnae_fiscal_secundaria"].fillna(""))
                for c in ["ddd_1","telefone_1","ddd_2","telefone_2","ddd_fax","fax","numero"]:
                    if c in df.columns:
                        d = helpers["digits_only"](df[c])
                        df[c] = d.mask(df[c].astype(str).str.strip().eq(""))
                invalid_ids = {}
                invalid_examples = {}
                if {"cnpj_basico","cnpj_ordem","cnpj_dv"}.issubset(df.columns):
                    cb = helpers["digits_only"](df["cnpj_basico"]).fillna("")
                    co = helpers["digits_only"](df["cnpj_ordem"]).fillna("")
                    dv = helpers["digits_only"](df["cnpj_dv"]).fillna("")
                    full = (cb + co + dv)
                    mask_full = full.str.len().eq(14)
                    valid_mask = mask_full & full.map(helpers["cnpj_valid"])
                    invalid_mask = mask_full & ~valid_mask
                    if invalid_mask.any():
                        invalid_ids["estabelecimentos_cnpj"] = int(invalid_mask.sum())
                        invalid_examples["estabelecimentos_cnpj"] = full.loc[invalid_mask].head(10).tolist()
                        masks["invalid_cnpj"] = invalid_mask
                if level == "aggressive":
                    if "cnae_fiscal_secundaria" in df.columns:
                        df["cnae_fiscal_secundaria"] = helpers["dedup_sort_pg_array"](df["cnae_fiscal_secundaria"]) 
                    if "correio_eletronico" in df.columns:
                        df["correio_eletronico"] = helpers["normalize_email_strict"](df["correio_eletronico"])
                    for c in ["ddd_1","ddd_2","ddd_fax"]:
                        if c in df.columns:
                            d = df[c].astype(str)
                            df[c] = d.where(d.str.len().isin([2,3]))
                    for c in ["telefone_1","telefone_2","fax"]:
                        if c in df.columns:
                            d = df[c].astype(str)
                            df[c] = d.where(d.str.len().between(8,11))
                    def _e164(ddd: str, phone: str) -> str | None:
                        if not ddd or not phone:
                            return None
                        if not ddd.isdigit() or not phone.isdigit():
                            return None
                        if len(ddd) not in (2,3):
                            return None
                        if len(phone) not in (8,9,10,11):
                            return None
                        return "+55" + ddd + phone
                    e164_examples = {}
                    pairs = [("ddd_1","telefone_1"),("ddd_2","telefone_2"),("ddd_fax","fax")]
                    for dcol, pcol in pairs:
                        if dcol in df.columns and pcol in df.columns:
                            dser = df[dcol].fillna("").astype(str)
                            pser = df[pcol].fillna("").astype(str)
                            ex = []
                            for i in range(min(len(df), 200)):
                                v = _e164(dser.iloc[i], pser.iloc[i])
                                if v:
                                    ex.append(v)
                                if len(ex) >= 10:
                                    break
                            if ex:
                                e164_examples[pcol] = ex
                    if e164_examples:
                        extras["e164_examples"] = e164_examples
                    if getattr(settings_obj, "enable_cep_enrichment", False):
                        m = helpers["get_cep_map"]()
                        if m is not None and "cep" in df.columns:
                            cep_digits = helpers["ensure_len"](df["cep"], 8)
                            key_col = None
                            if {"cep","uf","municipio_codigo"}.issubset(set(m.columns)):
                                key_col = "cep"
                                keys = cep_digits
                            elif {"cep_prefix","uf","municipio_codigo"}.issubset(set(m.columns)):
                                key_col = "cep_prefix"
                                keys = cep_digits.fillna("").astype(str).str[:5]
                            if key_col is not None:
                                # The join key has to be text on both sides. A map read
                                # without dtype=str turns an all-digit CEP into int64 and
                                # pandas refuses to merge str against int64.
                                keys = keys.fillna("").astype(str)
                                m_key = m.copy()
                                m_key[key_col] = m_key[key_col].fillna("").astype(str)
                                df_keys = pd.DataFrame({key_col: keys})
                                merged = df_keys.merge(m_key, on=key_col, how="left", suffixes=("","_map"))
                                if "municipio_codigo" in df.columns and "municipio_codigo" in merged.columns:
                                    mun = pd.to_numeric(df["municipio_codigo"], errors="coerce").astype("Int64")
                                    mun_map = pd.to_numeric(merged["municipio_codigo"], errors="coerce").astype("Int64")
                                    infer_mask = mun.isna() & mun_map.notna()
                                    if infer_mask.any():
                                        df.loc[infer_mask, "municipio_codigo"] = mun_map.loc[infer_mask]
                                        df.loc[infer_mask, "municipio_source"] = ("cep_map_" + ("exact" if key_col == "cep" else "prefix"))
                                        # update cep_enrichment metrics
                                        examples = pd.DataFrame({
                                            key_col: df_keys[key_col].loc[infer_mask].astype(str),
                                            "municipio_codigo": mun_map.loc[infer_mask].astype(str),
                                        }).head(10).to_dict(orient="records")
                                        cep_enrichment["municipio_inferred_count"] = int(infer_mask.sum())
                                        cep_enrichment["municipio_inferred_examples"] = examples
                                if "uf" in df.columns and "uf" in merged.columns:
                                    # Keep the nulls. astype(str) would turn the NaN of an
                                    # unmatched row into the literal "nan" and write that
                                    # over a valid UF.
                                    uf_map = merged["uf"]
                                    uf_cur = df["uf"]
                                    only_if_null = getattr(settings_obj, "cep_correct_uf_only_if_null", True)
                                    # Only rows the map actually matched may be touched.
                                    corr_mask = uf_map.notna() & (
                                        uf_cur.isna()
                                        if only_if_null
                                        else (uf_cur.isna() | (uf_cur.astype(str) != uf_map.astype(str)))
                                    )
                                    if corr_mask.any():
                                        df.loc[corr_mask, "uf"] = uf_map.loc[corr_mask]
                                        df.loc[corr_mask, "uf_source"] = ("cep_map_" + ("exact" if key_col == "cep" else "prefix"))
                                        # update cep_enrichment metrics
                                        examples = pd.DataFrame({
                                            key_col: df_keys[key_col].loc[corr_mask].astype(str),
                                            "uf_before": uf_cur.loc[corr_mask].astype(str),
                                            "uf_map": uf_map.loc[corr_mask].astype(str),
                                        }).head(10).to_dict(orient="records")
                                        cep_enrichment["uf_corrected_count"] = int(corr_mask.sum())
                                        cep_enrichment["uf_corrected_examples"] = examples
                mun_map = helpers["get_mun_name_map"]()
                if mun_map is not None and "municipio_nome" in df.columns:
                    cols = set(mun_map.columns)
                    mm = mun_map.copy()
                    mm["municipio_nome_norm"] = helpers["normalize_name_series"](mm["municipio_nome"])
                    if "uf" in cols:
                        mm["uf_norm"] = mm["uf"].astype(str).str.strip().str.upper()
                    dn = pd.DataFrame({
                        "municipio_nome_norm": helpers["normalize_name_series"](df["municipio_nome"]) 
                    })
                    if "uf" in df.columns and "uf_norm" in mm.columns:
                        dn["uf_norm"] = df["uf"].astype(str).str.strip().str.upper()
                        merged = dn.merge(mm, on=["municipio_nome_norm","uf_norm"], how="left")
                    else:
                        merged = dn.merge(mm, on=["municipio_nome_norm"], how="left")
                    if "municipio_codigo" in df.columns and "municipio_codigo" in merged.columns:
                        mun = pd.to_numeric(df["municipio_codigo"], errors="coerce").astype("Int64")
                        mun_map2 = pd.to_numeric(merged["municipio_codigo"], errors="coerce").astype("Int64")
                        infer2 = mun.isna() & mun_map2.notna()
                        if infer2.any():
                            df.loc[infer2, "municipio_codigo"] = mun_map2.loc[infer2]
                            df.loc[infer2, "municipio_source"] = "mun_name_map"
                            # update cep_enrichment metrics
                            examples = pd.DataFrame({
                                "municipio_nome": df["municipio_nome"].loc[infer2].astype(str),
                                "municipio_codigo": mun_map2.loc[infer2].astype(str),
                            }).head(10).to_dict(orient="records")
                            cep_enrichment["municipio_inferred_by_name_count"] = int(infer2.sum())
                            cep_enrichment["municipio_inferred_by_name_examples"] = examples
                if invalid_ids:
                    extras["invalid_ids"] = invalid_ids
                    extras["invalid_id_examples"] = invalid_examples
                if cep_enrichment:
                    extras["cep_enrichment"] = cep_enrichment
                return df, masks, extras


class SociosSchema(SchemaModel):
        cnpj_basico: PanderaSeries[str]
        identificador_socio: PanderaSeries[pd.Int64Dtype]
        nome_socio_ou_razao_social: PanderaSeries[str]
        cnpj_cpf_socio: PanderaSeries[str]
        qualificacao_socio_codigo: PanderaSeries[pd.Int64Dtype]
        data_entrada_sociedade: PanderaSeries[object]
        pais_codigo: PanderaSeries[pd.Int64Dtype]
        representante_legal_cpf: PanderaSeries[str]
        nome_representante_legal: PanderaSeries[str]
        qualificacao_representante_legal_codigo: PanderaSeries[pd.Int64Dtype]
        faixa_etaria: PanderaSeries[pd.Int64Dtype]


        @classmethod
        def clean(cls, df: pd.DataFrame, level: str, settings_obj, helpers) -> tuple[pd.DataFrame, dict[str, pd.Series], dict[str, Any]]:
                if "cnpj_basico" in df.columns:
                        df["cnpj_basico"] = helpers["ensure_len"](df["cnpj_basico"], 8)
                if "cnpj_cpf_socio" in df.columns:
                        d = helpers["digits_only"](df["cnpj_cpf_socio"])
                        mask_len = d.str.len().isin([11,14])
                        def _ok(x: str) -> bool:
                                if len(x) == 11:
                                        return helpers["cpf_valid"](x)
                                if len(x) == 14:
                                        return helpers["cnpj_valid"](x)
                                return False
                        valid = d.where(mask_len).map(lambda x: _ok(x) if isinstance(x, str) else False)
                        df["cnpj_cpf_socio"] = d.where(valid)
                if "representante_legal_cpf" in df.columns:
                        cpf = helpers["ensure_len"](df["representante_legal_cpf"], 11)
                        df["representante_legal_cpf"] = cpf.where(cpf.map(lambda x: helpers["cpf_valid"](x) if isinstance(x, str) else False))
                for c in ["nome_socio_ou_razao_social","nome_representante_legal"]:
                        if c in df.columns:
                                df[c] = helpers["strip_empty_to_na"](df[c])
                return df, {}, {}


class SimplesSchema(SchemaModel):
        cnpj_basico: PanderaSeries[str]
        opcao_pelo_simples: PanderaSeries[str]
        data_opcao_pelo_simples: PanderaSeries[object]
        data_exclusao_do_simples: PanderaSeries[object]
        opcao_pelo_mei: PanderaSeries[str]
        data_opcao_pelo_mei: PanderaSeries[object]
        data_exclusao_do_mei: PanderaSeries[object]
        


        @classmethod
        def clean(cls, df: pd.DataFrame, level: str, settings_obj, helpers) -> tuple[pd.DataFrame, dict[str, pd.Series], dict[str, Any]]:
                if "cnpj_basico" in df.columns:
                        df["cnpj_basico"] = helpers["ensure_len"](df["cnpj_basico"], 8)
                return df, {}, {}

        

DIFF_COLUMNS_MAP: dict[str, list[str]] = {
    "empresas": ["cnpj_basico","razao_social"],
    "estabelecimentos": [
        "cnpj_basico","cnpj_ordem","cnpj_dv","uf","cep",
        "cnae_fiscal_principal_codigo","cnae_fiscal_secundaria","correio_eletronico"
    ],
    "socios": ["cnpj_basico","cnpj_cpf_socio","representante_legal_cpf"],
    "simples": ["cnpj_basico"],
}

SCHEMA_MAP: dict[str, type[SchemaModel]] = {
    "empresas": EmpresasSchema,
    "estabelecimentos": EstabelecimentosSchema,
    "socios": SociosSchema,
    "simples": SimplesSchema,
}


def validate(config_name: str, df: pd.DataFrame):
    logger = logging.getLogger(__name__)
    level = getattr(settings, "auto_repair_level", "basic")
    if level == "none":
        return df, {"level": level, "after_nulls": {}, "null_deltas": {}, "columns": list(df.columns)}, {}
    before_nulls = {c: int(df[c].isna().sum()) for c in df.columns}
    cols_for_diff = []
    if level == "aggressive":
        cols_for_diff = DIFF_COLUMNS_MAP.get(config_name, [])
    snapshot = {c: df[c].astype(str).copy() for c in cols_for_diff if c in df.columns}
    masks: dict[str, pd.Series] = {}

    helpers = {
        "cpf_valid": _cpf_valid,
        "cnpj_valid": _cnpj_valid,
        "digits_only": _digits_only,
        "strip_empty_to_na": _strip_empty_to_na,
        "ensure_len": _ensure_len,
        "normalize_uf": _normalize_uf,
        "normalize_email": _normalize_email,
        "normalize_email_strict": _normalize_email_strict,
        "normalize_cnae_code": _normalize_cnae_code,
        "normalize_pg_array_digits": _normalize_pg_array_digits,
        "dedup_sort_pg_array": _dedup_sort_pg_array,
        "get_cep_map": _get_cep_map,
        "get_mun_name_map": _get_mun_name_map,
        "normalize_name_series": _normalize_name_series,
    }
    schema = SCHEMA_MAP.get(config_name)
    extras: dict[str, Any] = {}
    if schema is not None:
        df, masks, extras = schema.clean(df, level, settings, helpers)

    after_nulls = {c: int(df[c].isna().sum()) for c in df.columns}
    deltas = {c: after_nulls[c] - before_nulls.get(c, 0) for c in df.columns}
    repaired = {c: v for c, v in after_nulls.items() if v}
    if repaired:
        logger.info(f"Auto-repair: nullified/cleaned values per column: {repaired}")
    sample_diffs = {}
    if level == "aggressive" and snapshot:
        for c, before in snapshot.items():
            if c in df.columns:
                after = df[c].astype(str)
                changed = before != after
                if changed.any():
                    pairs = (
                        pd.DataFrame({"before": before, "after": after})
                        .loc[changed]
                        .dropna()
                        .head(10)
                        .to_dict(orient="records")
                    )
                    if pairs:
                        sample_diffs[c] = pairs
    changed_counts = {}
    if level == "aggressive" and snapshot:
        for c, before in snapshot.items():
            if c in df.columns:
                after = df[c].astype(str)
                changed = (before != after)
                cnt = int(changed.sum())
                if cnt:
                    changed_counts[c] = cnt
    telemetry = {
        "level": level,
        "after_nulls": after_nulls,
        "null_deltas": deltas,
        "columns": list(df.columns),
        "sample_diffs": sample_diffs,
        "changed_counts": changed_counts,
    }
    if extras:
        telemetry.update(extras)
    return df, telemetry, masks
