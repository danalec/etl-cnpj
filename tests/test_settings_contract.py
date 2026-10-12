"""Contract between `Settings` and `.env.example`.

`AGENTS.md` says a new configuration option goes to `Settings` *and* to
`.env.example`, and `docs/auto-repair.md` now states that every setting appears
there. That claim drifted before: the doc warned that variables were missing
from `.env.example` when they were not, and `rfb_base_url` was in fact missing
while nobody noticed. The drift is silent in both directions, so it is pinned
here rather than left to review.
"""

import re
from pathlib import Path

import pytest

from src.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"

# Derived from the file location, not something a user sets.
INTERNAL = {"project_root"}


def _declared_env_names() -> set[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    # A commented-out line counts as documented; the optional settings are all
    # commented out with their default next to them.
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)\s*=", text, re.MULTILINE))


@pytest.mark.parametrize(
    "field", sorted(set(Settings.model_fields) - INTERNAL), ids=lambda f: f
)
def test_every_setting_is_documented_in_env_example(field):
    assert field.upper() in _declared_env_names(), (
        f"{field} existe em Settings mas nao no .env.example"
    )


def test_env_example_declares_nothing_that_settings_does_not_have():
    known = {name.lower() for name in Settings.model_fields}
    unknown = sorted(n for n in _declared_env_names() if n.lower() not in known)
    assert unknown == [], f"no .env.example sem campo correspondente: {unknown}"


def test_log_directories_exist_and_auto_repair_is_gone(tmp_project):
    """logs/auto_repair was created on every boot and never written to."""
    settings = tmp_project
    assert settings.telemetry_dir.is_dir()
    assert settings.quarantine_dir.is_dir()
    assert not (settings.log_dir / "auto_repair").exists()
    assert not hasattr(settings, "auto_repair_dir")