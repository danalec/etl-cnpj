from pathlib import Path

import pytest

import src.check_update as cu
from src.settings import settings


def test_get_latest_remote_date_html(monkeypatch):
    html = """
    <html><body>
    <a href="2025-10/">2025-10/</a>
    <a href="2025-11/">2025-11/</a>
    <a href="2024-12/">2024-12/</a>
    </body></html>
    """
    class FakeResp:
        text = html
        def raise_for_status(self):
            return None
    monkeypatch.setattr(cu.requests, "get", lambda url, timeout=30: FakeResp())
    d = cu.get_latest_remote_date()
    assert d == "2025-11"


def test_get_latest_remote_date_none(monkeypatch):
    html = "<html><body><a href=\"foo\">foo</a></body></html>"
    class FakeResp:
        text = html
        def raise_for_status(self):
            return None
    monkeypatch.setattr(cu.requests, "get", lambda url, timeout=30: FakeResp())
    d = cu.get_latest_remote_date()
    assert d is None


def test_clean_data_dirs(tmp_path, monkeypatch):
    settings.project_root = Path(tmp_path)
    settings.create_dirs()
    (settings.compressed_dir / "a.zip").write_text("x")
    (settings.extracted_dir / "dir1").mkdir(parents=True, exist_ok=True)
    (settings.extracted_dir / "dir1" / "f.txt").write_text("y")
    (settings.extracted_dir / "b.txt").write_text("z")
    cu.clean_data_dirs()
    assert list(settings.compressed_dir.glob("*")) == []
    assert list(settings.extracted_dir.glob("*")) == []


def test_check_updates_updates_target_date(tmp_path, monkeypatch):
    settings.project_root = Path(tmp_path)
    settings.create_dirs()
    (settings.state_file).write_text("2025-09")
    html = """
    <a href="2025-10/">2025-10/</a>
    <a href="2025-11/">2025-11/</a>
    """
    class FakeResp:
        text = html
        def raise_for_status(self):
            return None
    monkeypatch.setattr(cu.requests, "get", lambda url, timeout=30: FakeResp())
    d = cu.check_updates(skip_clean=True)
    assert d == "2025-11"
    assert settings.target_date == "2025-11"


def test_target_date_is_adopted_even_when_already_processed(tmp_path, monkeypatch):
    """download_url is derived from target_date, so it must never be left empty.

    Persisting the processed version made check_updates return early on the next
    run, before assigning target_date, and --force reaches the pipeline anyway:
    the downloader then requested an empty URL.
    """
    settings.project_root = Path(tmp_path)
    settings.create_dirs()

    monkeypatch.setattr(cu, "get_latest_remote_date", lambda: "2025-11")
    monkeypatch.setattr(cu, "get_local_version", lambda: "2025-11")
    monkeypatch.setattr(cu, "clean_data_dirs", lambda: pytest.fail("must not clean"))

    assert cu.check_updates() is None  # nothing new, so nothing to clean
    assert settings.target_date == "2025-11"
    assert settings.download_url.endswith("2025-11/")


def test_resume_ledger_follows_the_month_being_processed(tmp_path, monkeypatch):
    """--resume must key on the folder being processed, not the previous one.

    The module-level state object is built at import from
    last_version_processed.txt. Without re-pointing it, resuming in a new month
    would skip the previous month's completed stages and reload its data under
    the new month's name.
    """
    import sys

    import src.__main__ as m
    import src.state as st

    settings.project_root = Path(tmp_path)
    settings.create_dirs()
    (settings.state_file).write_text("2025-11")
    st.PipelineState("2025-11").update("download", "completed")

    monkeypatch.setattr(cu, "get_latest_remote_date", lambda: "2025-12")
    monkeypatch.setattr(cu, "get_local_version", lambda: "2025-11")
    monkeypatch.setattr(cu, "clean_data_dirs", lambda: None)
    monkeypatch.setattr(cu, "update_local_version", lambda v: None)
    ran = []
    monkeypatch.setattr("src.downloader.run_download", lambda: ran.append("download"))
    monkeypatch.setattr("src.extract_files.run_extraction", lambda: ran.append("extract"))
    monkeypatch.setattr("src.database_loader.run_loader", lambda: ran.append("load"))
    monkeypatch.setattr("src.database_loader.run_constraints", lambda: ran.append("constraints"))
    monkeypatch.setattr(sys, "argv", ["prog", "--resume"])
    monkeypatch.setattr(m, "state", st.PipelineState("2025-11"))

    m.main()

    ledger = st._read()
    assert "2025-12" in ledger, "resume ledger was not re-keyed on the new month"
    assert ran == ["download", "extract", "load", "constraints"], (
        f"the new month's stages were skipped, ran={ran}"
    )


def test_step_check_never_deletes_the_downloads(tmp_path, monkeypatch):
    """`--step check` is a query, so it must not wipe the data.

    It called ``check_updates()`` with the default ``skip_clean=False``, so
    merely asking "is there a new month?" deleted every archive in
    compressed_files and every extracted volume — tens of GB that then had to be
    downloaded again. ``--resume`` and ``--dry-run`` were already protected;
    this path was not.
    """
    import sys

    import src.__main__ as m

    settings.project_root = Path(tmp_path)
    settings.create_dirs()
    (settings.compressed_dir / "a.zip").write_text("x")
    (settings.extracted_dir / "empresas").mkdir(parents=True, exist_ok=True)
    (settings.extracted_dir / "empresas" / "f.csv").write_text("y")

    monkeypatch.setattr(cu, "get_latest_remote_date", lambda: "2025-11")
    monkeypatch.setattr(cu, "get_local_version", lambda: "2025-10")
    monkeypatch.setattr(sys, "argv", ["prog", "--step", "check"])

    m.main()

    assert (settings.compressed_dir / "a.zip").exists(), "--step check deleted the archives"
    assert (settings.extracted_dir / "empresas" / "f.csv").exists(), (
        "--step check deleted the extracted volumes"
    )
