import pandas as pd

from src.database_loader import sanitize_dates, clean_estabelecimentos_chunk, clean_empresas_chunk


def test_sanitize_dates_coerce_invalid():
    df = pd.DataFrame({
        "d1": ["20250101", "invalid"],
        "d2": ["20241231", None],
    })
    out = sanitize_dates(df.copy(), ["d1", "d2"])
    assert pd.api.types.is_datetime64_any_dtype(out["d1"])  
    assert pd.notna(out.loc[0, "d1"]) and pd.isna(out.loc[1, "d1"])  
    assert pd.api.types.is_datetime64_any_dtype(out["d2"]) and pd.isna(out.loc[1, "d2"])  


def test_clean_empresas_chunk_capital_social_decimal():
    df = pd.DataFrame({
        "capital_social": ["123,45", "0,00", None],
    })
    out = clean_empresas_chunk(df.copy())
    assert out["capital_social"].iloc[0] == 123.45
    assert out["capital_social"].iloc[1] == 0.0
    assert pd.isna(out["capital_social"].iloc[2])


def test_clean_estabelecimentos_chunk_cnaes_array():
    df = pd.DataFrame({
        "cnae_fiscal_secundaria": ["1234567, 9876543", "", None],
    })
    out = clean_estabelecimentos_chunk(df.copy())
    assert out["cnae_fiscal_secundaria"].iloc[0] == "{1234567,9876543}"
    assert out["cnae_fiscal_secundaria"].iloc[1] is None
    assert out["cnae_fiscal_secundaria"].iloc[2] is None

