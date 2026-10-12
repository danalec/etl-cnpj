import pandas as pd

from src.settings import settings
from src.validation import validate


def test_validate_empresas_basic():
    settings.auto_repair_level = "basic"
    df = pd.DataFrame({
        "cnpj_basico": ["12345678","123"],
        "razao_social": [" Empresa ","  "],
        "ente_federativo_responsavel": [" ","abc"],
    })
    out, tel, masks = validate("empresas", df.copy())
    assert out["cnpj_basico"].iloc[0] == "12345678"
    assert pd.isna(out["cnpj_basico"].iloc[1])
    assert out["razao_social"].iloc[0] == "Empresa"
    assert pd.isna(out["razao_social"].iloc[1])
    assert pd.isna(out["ente_federativo_responsavel"].iloc[0])
    assert out["ente_federativo_responsavel"].iloc[1] == "abc"


def test_validate_estabelecimentos_basic():
    settings.auto_repair_level = "basic"
    df = pd.DataFrame({
        "cnpj_basico": ["12345678"],
        "cnpj_ordem": ["0012"],
        "cnpj_dv": ["34"],
        "correio_eletronico": ["bad@@mail"],
        "cep": ["12.345-678"],
        "uf": ["sp"],
        "cnae_fiscal_principal_codigo": ["1234567"],
        "cnae_fiscal_secundaria": ["11;22,abc1234567"],
        "ddd_1": ["0a2"],
        "telefone_1": ["111-222-333"],
    })
    out, tel, masks = validate("estabelecimentos", df.copy())
    assert out["cnpj_basico"].iloc[0] == "12345678"
    assert out["cnpj_ordem"].iloc[0] == "0012"
    assert out["cnpj_dv"].iloc[0] == "34"
    assert pd.isna(out["correio_eletronico"].iloc[0])
    assert out["cep"].iloc[0] == "12345678"
    assert out["uf"].iloc[0] == "SP"
    assert int(out["cnae_fiscal_principal_codigo"].iloc[0]) == 1234567
    assert out["cnae_fiscal_secundaria"].iloc[0].startswith("{")
    assert out["ddd_1"].iloc[0] == "02"
    assert out["telefone_1"].iloc[0] == "111222333"


def test_validate_estabelecimentos_phone_branches():
    settings.auto_repair_level = "basic"
    df = pd.DataFrame({
        "ddd_1": ["2","02","002"],
        "telefone_1": ["123456789","111-222-333","(11) 222333"],
    })
    out, _, _ = validate("estabelecimentos", df.copy())
    assert out["ddd_1"].tolist() == ["2","02","002"]
    assert out["telefone_1"].tolist() == ["123456789","111222333","11222333"]


def test_validate_estabelecimentos_aggressive():
    settings.auto_repair_level = "aggressive"
    df = pd.DataFrame({
        "cnae_fiscal_secundaria": ["{0000002,0000001,0000002}"],
        "correio_eletronico": ["USER@ExAmple.com"],
        "ddd_1": ["02"],
        "telefone_1": ["111222333"],
    })
    out, tel, masks = validate("estabelecimentos", df.copy())
    assert out["cnae_fiscal_secundaria"].iloc[0] == "{1,2}"
    assert out["correio_eletronico"].iloc[0] == "user@example.com"
    assert out["ddd_1"].iloc[0] in ("02","2")
    assert out["telefone_1"].iloc[0] in ("111222333","111222333")


def test_validate_socios_basic():
    settings.auto_repair_level = "basic"
    df = pd.DataFrame({
        "cnpj_basico": ["12345678"],
        "cnpj_cpf_socio": ["123.456.789-09"],
        "representante_legal_cpf": ["000.000.000-00"],
        "nome_socio_ou_razao_social": ["  "],
    })
    out, tel, masks = validate("socios", df.copy())
    assert out["cnpj_basico"].iloc[0] == "12345678"
    assert out["cnpj_cpf_socio"].iloc[0] is None or isinstance(out["cnpj_cpf_socio"].iloc[0], str)
    assert pd.isna(out["representante_legal_cpf"].iloc[0])
    assert pd.isna(out["nome_socio_ou_razao_social"].iloc[0])


def test_validate_simples_basic():
    settings.auto_repair_level = "basic"
    df = pd.DataFrame({
        "cnpj_basico": ["12"],
    })
    out, tel, masks = validate("simples", df.copy())
    assert pd.isna(out["cnpj_basico"].iloc[0])
