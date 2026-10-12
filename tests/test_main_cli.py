import sys
from types import SimpleNamespace

import src.__main__ as m


def test_main_step_check(monkeypatch):
    called = []
    monkeypatch.setattr("src.check_update.check_updates", lambda *a, **k: called.append("check"))
    argv = ["prog", "--step", "check"]
    monkeypatch.setattr(sys, "argv", argv)
    m.main()
    assert called == ["check"]


def test_main_step_download(monkeypatch):
    called = []
    monkeypatch.setattr("src.check_update.check_updates", lambda *a, **k: None)
    monkeypatch.setattr("src.downloader.run_download", lambda: called.append("download"))
    argv = ["prog", "--step", "download"]
    monkeypatch.setattr(sys, "argv", argv)
    m.main()
    assert called == ["download"]


def test_dry_run_never_asks_to_clean_downloaded_data(monkeypatch, tmp_path):
    """--dry-run promised to change nothing, but check_updates ran first.

    With skip_clean=False check_updates calls clean_data_dirs(), which deletes
    everything under data/compressed_files and data/extracted_files as soon as a
    newer release exists upstream. The contract under test is that a dry run
    always passes skip_clean=True.
    """
    from pathlib import Path

    from src.settings import settings

    settings.project_root = Path(tmp_path)
    settings.create_dirs()
    compressed = Path(settings.compressed_dir)
    compressed.mkdir(parents=True, exist_ok=True)
    victim = compressed / "empresas01.zip"
    victim.write_bytes(b"payload")

    seen = []
    monkeypatch.setattr(
        "src.check_update.check_updates",
        lambda skip_clean=False: seen.append(skip_clean) or "2025-11",
    )
    monkeypatch.setattr(sys, "argv", ["prog", "--dry-run"])
    m.main()

    assert seen == [True], "dry run would let check_updates wipe the data dirs"
    assert victim.exists()


def test_main_full_pipeline_force(monkeypatch):
    calls = []
    monkeypatch.setattr("src.check_update.check_updates", lambda *a, **k: None)
    monkeypatch.setattr("src.downloader.run_download", lambda: calls.append("download"))
    monkeypatch.setattr("src.extract_files.run_extraction", lambda: calls.append("extract"))
    monkeypatch.setattr("src.database_loader.run_loader", lambda: calls.append("load"))
    monkeypatch.setattr("src.database_loader.run_constraints", lambda: calls.append("constraints"))
    dummy_state = SimpleNamespace(
        should_skip=lambda step: False,
        update=lambda step, status: None,
    )
    monkeypatch.setattr(m, "state", dummy_state)
    argv = ["prog", "--force"]
    monkeypatch.setattr(sys, "argv", argv)
    m.main()
    assert calls == ["download", "extract", "load", "constraints"]
