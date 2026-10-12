from pathlib import Path

import src.state as st
from src.settings import settings


def test_state_persistence(tmp_path):
    settings.project_root = Path(tmp_path)
    settings.create_dirs()
    st.PipelineState("2025-11")
    st.start_run("2025-11")
    st.mark_stage("2025-11", "download", "completed")
    m = st.print_status("2025-11", return_map=True)
    assert m["download"] == "completed"


def test_should_skip(tmp_path):
    settings.project_root = Path(tmp_path)
    settings.create_dirs()
    s = st.PipelineState("2025-11")
    s.update("download", "completed")
    assert s.should_skip("download") is True
    assert s.should_skip("extract") is False
