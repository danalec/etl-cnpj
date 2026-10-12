import pandas as pd

from src.settings import settings
from src.validation import validate


def test_estabelecimentos_invalid_cnpj_masks_telemetry():
    settings.auto_repair_level = "basic"
    df = pd.DataFrame({
        "cnpj_basico": ["12345678","87654321"],
        "cnpj_ordem": ["0001","0001"],
        "cnpj_dv": ["00","00"],
    })
    out, tel, masks = validate("estabelecimentos", df.copy())
    assert "invalid_cnpj" in masks
    invalid_count = int(masks["invalid_cnpj"].sum())
    assert "invalid_ids" in tel and "invalid_id_examples" in tel
    assert invalid_count == tel["invalid_ids"]["estabelecimentos_cnpj"]
    assert isinstance(tel["invalid_id_examples"]["estabelecimentos_cnpj"], list)


def test_estabelecimentos_aggressive_e164_examples():
    settings.auto_repair_level = "aggressive"
    df = pd.DataFrame({
        "ddd_1": ["11","21","031"],
        "telefone_1": ["999888777","2223334444","12345678"],
    })
    out, tel, masks = validate("estabelecimentos", df.copy())
    assert "e164_examples" in tel
    ex = tel["e164_examples"].get("telefone_1", [])
    assert isinstance(ex, list)
    assert any(isinstance(x, str) and x.startswith("+55") for x in ex)


def test_estabelecimentos_aggressive_snapshot_counts():
    settings.auto_repair_level = "aggressive"
    df = pd.DataFrame({
        "cnpj_basico": ["12345678"],
        "cnpj_ordem": ["0001"],
        "cnpj_dv": ["23"],
        "uf": ["sp"],
        "cep": ["12345678"],
        "cnae_fiscal_principal_codigo": ["1234567"],
        "cnae_fiscal_secundaria": ["{0000002,0000001}"],
        "correio_eletronico": ["USER@ExAmple.com"],
    })
    out, tel, masks = validate("estabelecimentos", df.copy())
    assert "changed_counts" in tel and "sample_diffs" in tel
    assert tel["changed_counts"].get("correio_eletronico", 0) >= 1
    diffs = tel["sample_diffs"].get("correio_eletronico", [])
    assert isinstance(diffs, list)
    assert len(diffs) >= 1
