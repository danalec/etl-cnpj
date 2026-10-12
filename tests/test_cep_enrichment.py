"""Enrichment by CEP.

Every test here used to crash or corrupt data. The block was never covered, so
``ENABLE_CEP_ENRICHMENT=true`` was unusable: it raised ``UnboundLocalError`` on
ordinary chunks, and in ``CEP_CORRECT_UF_ONLY_IF_NULL=false`` it overwrote a
valid UF with the literal string ``"nan"``.
"""

import pandas as pd
import pytest

from src import validation
from src.settings import settings
from src.validation import validate


@pytest.fixture(autouse=True)
def clear_cep_cache():
    """The maps are cached in module globals and never invalidated.

    Every test here swaps the map, so a stale one from a previous test would
    silently change what is being asserted.
    """
    validation.CEP_MAP_CACHE = None
    validation.MUN_NAME_MAP_CACHE = None
    yield
    validation.CEP_MAP_CACHE = None
    validation.MUN_NAME_MAP_CACHE = None


@pytest.fixture
def cep_map(tmp_path):
    """Write a CEP map and point the settings at it."""

    def _write(text: str) -> None:
        path = tmp_path / "cep_map.csv"
        path.write_text(text, encoding="utf-8")
        settings.cep_map_path = path

    return _write


def _df(**over):
    row = {
        "cnpj_basico": ["12345678"],
        "cnpj_ordem": ["0012"],
        "cnpj_dv": ["34"],
        "cep": ["12345678"],
    }
    row.update(over)
    return pd.DataFrame(row)


def test_numeric_cep_map_does_not_break_the_merge(cep_map):
    """An all-digit CEP column parses as int64 unless the map is read as text."""
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    cep_map("cep,uf,municipio_codigo\n12345678,SP,3550308\n")

    out, _, _ = validate("estabelecimentos", _df(uf=[None], municipio_codigo=[None]))

    assert out["uf"].iloc[0] == "SP"
    assert out["municipio_source"].iloc[0] == "cep_map_exact"


def test_chunk_with_nothing_to_fix_does_not_raise(cep_map):
    """corr_mask.all() == False left `examples` unbound and killed the chunk."""
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    cep_map("cep,uf,municipio_codigo\n12345678,SP,3550308\n")

    # UF already correct and municipio already filled: nothing to correct.
    out, telemetry, _ = validate(
        "estabelecimentos", _df(uf=["SP"], municipio_codigo=[3550308])
    )

    assert out["uf"].iloc[0] == "SP"
    # No correction happened, so the counter must be absent rather than wrong.
    assert "cep_enrichment" not in telemetry


def test_frame_without_uf_column_does_not_raise(cep_map):
    """`corr_mask` was only assigned inside the uf branch, then read outside."""
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    cep_map("cep,uf,municipio_codigo\n12345678,SP,3550308\n")

    out, _, _ = validate("estabelecimentos", _df(municipio_codigo=[3550308]))

    assert out["municipio_codigo"].iloc[0] == 3550308


def test_unmatched_cep_does_not_overwrite_a_valid_uf(cep_map):
    """Regression: the NaN of an unmatched row became "nan" and overwrote "SP"."""
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    settings.cep_correct_uf_only_if_null = False
    cep_map("cep,uf,municipio_codigo\n99999999,RJ,3304557\n")

    out, _, _ = validate("estabelecimentos", _df(uf=["SP"]))

    assert out["uf"].iloc[0] == "SP"
    assert "uf_source" not in out.columns or pd.isna(out["uf_source"].iloc[0])


def test_enrichment_infers_missing_municipio(cep_map):
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    cep_map("cep,uf,municipio_codigo\n12345678,SP,3550308\n")

    out, telemetry, _ = validate("estabelecimentos", _df(uf=["SP"], municipio_codigo=[None]))

    assert int(out["municipio_codigo"].iloc[0]) == 3550308
    assert out["municipio_source"].iloc[0] == "cep_map_exact"
    assert telemetry["cep_enrichment"]["municipio_inferred_count"] == 1


def test_prefix_map_infers_municipio(cep_map):
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    cep_map("cep_prefix,uf,municipio_codigo\n12345,SP,3550308\n")

    out, _, _ = validate("estabelecimentos", _df(uf=[None], municipio_codigo=[None]))

    assert int(out["municipio_codigo"].iloc[0]) == 3550308
    assert out["municipio_source"].iloc[0] == "cep_map_prefix"
    assert out["uf"].iloc[0] == "SP"


def test_null_uf_is_filled_when_only_if_null_is_true(cep_map):
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    settings.cep_correct_uf_only_if_null = True
    cep_map("cep,uf,municipio_codigo\n12345678,SP,3550308\n")

    out, telemetry, _ = validate("estabelecimentos", _df(uf=[None]))

    assert out["uf"].iloc[0] == "SP"
    assert out["uf_source"].iloc[0] == "cep_map_exact"
    assert telemetry["cep_enrichment"]["uf_corrected_count"] == 1


def test_only_if_null_keeps_a_diverging_uf(cep_map):
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    settings.cep_correct_uf_only_if_null = True
    cep_map("cep,uf,municipio_codigo\n12345678,SP,3550308\n")

    out, _, _ = validate("estabelecimentos", _df(uf=["RJ"]))

    assert out["uf"].iloc[0] == "RJ"


def test_enrichment_enabled_without_a_map_does_not_raise():
    """The counter lines sat outside the `m is not None` guard."""
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    settings.cep_map_path = None

    out, _, _ = validate("estabelecimentos", _df(uf=["SP"]))

    assert out["uf"].iloc[0] == "SP"


def test_changing_the_map_path_reloads_the_map(tmp_path, cep_map):
    """The cache was keyed on "is there a cache", not on which file it holds."""
    settings.auto_repair_level = "aggressive"
    settings.enable_cep_enrichment = True
    cep_map("cep,uf,municipio_codigo\n12345678,SP,3550308\n")
    validate("estabelecimentos", _df(uf=[None], municipio_codigo=[None]))
    assert settings.state_file.parent.exists()

    other = tmp_path / "outro_mapa.csv"
    other.write_text("cep,uf,municipio_codigo\n12345678,RJ,3304557\n", encoding="utf-8")
    settings.cep_map_path = other

    out, _, _ = validate("estabelecimentos", _df(uf=[None], municipio_codigo=[None]))

    assert out["uf"].iloc[0] == "RJ"
    assert int(out["municipio_codigo"].iloc[0]) == 3304557