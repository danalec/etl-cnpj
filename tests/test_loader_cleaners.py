import pandas as pd
from pathlib import Path

from src.database_loader import sanitize_dates, clean_estabelecimentos_chunk, clean_empresas_chunk, clean_socios_chunk, clean_simples_chunk, _detect_encoding


def test_sanitize_dates_basic():
    df = pd.DataFrame({"d":["20240101","20241301"," ",None]})
    out = sanitize_dates(df.copy(), ["d"]) 
    assert str(out["d"].iloc[0]).startswith("2024-01-01")
    assert pd.isna(out["d"].iloc[1])


def test_clean_estabelecimentos_chunk_pg_array_and_dates():
    df = pd.DataFrame({
        "cnae_fiscal_secundaria": ["1;2,3"],
        "data_inicio_atividade": ["20240101"],
    })
    out = clean_estabelecimentos_chunk(df.copy())
    assert out["cnae_fiscal_secundaria"].iloc[0] == "{1,2,3}"
    assert str(out["data_inicio_atividade"].iloc[0]).startswith("2024-01-01")


def test_clean_empresas_chunk_capital_social():
    df = pd.DataFrame({"capital_social":["1.234,56","abc",None," ","nan","None"]})
    out = clean_empresas_chunk(df.copy())
    assert float(out["capital_social"].iloc[0]) == 1234.56
    assert pd.isna(out["capital_social"].iloc[1])
    assert pd.isna(out["capital_social"].iloc[2])
    assert pd.isna(out["capital_social"].iloc[3])
    assert pd.isna(out["capital_social"].iloc[4])
    assert pd.isna(out["capital_social"].iloc[5])


def test_clean_socios_and_simples_dates():
    df1 = pd.DataFrame({"data_entrada_sociedade":["20231231"]})
    out1 = clean_socios_chunk(df1.copy())
    assert str(out1["data_entrada_sociedade"].iloc[0]).startswith("2023-12-31")
    df2 = pd.DataFrame({"data_opcao_pelo_simples":["20230101"],"data_opcao_pelo_mei":["20230102"]})
    out2 = clean_simples_chunk(df2.copy())
    assert str(out2["data_opcao_pelo_simples"].iloc[0]).startswith("2023-01-01")


def test_detect_encoding_default(tmp_path):
    p = Path(tmp_path) / "x.csv"
    p.write_text("a;b\n1;2\n", encoding="utf-8")
    enc = _detect_encoding(p, "latin1")
    assert enc in ("UTF-8","utf_8","utf-8","latin1","ascii")
