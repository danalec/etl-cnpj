import json
import logging
from pathlib import Path
from typing import Literal, Optional
from .settings import settings

logger = logging.getLogger(__name__)

STAGES = ["check", "download", "extract", "load", "constraints"]


def _file() -> Path:
    return settings.data_dir / "runs.json"


def _read() -> dict:
    p = _file()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(d: dict) -> None:
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def start_run(date: str) -> None:
    d = _read()
    if date not in d:
        d[date] = {s: "pending" for s in STAGES}
    _write(d)


def mark_stage(date: str, stage: str, status: str) -> None:
    d = _read()
    if date not in d:
        d[date] = {s: "pending" for s in STAGES}
    d[date][stage] = status
    _write(d)


def stages_not_completed(date: str) -> list[str]:
    """Stages of ``date`` that did not reach ``completed``."""
    m = _read().get(date, {})
    return [s for s in STAGES if m.get(s, "pending") != "completed"]


def is_month_complete(date: str) -> bool:
    """Whether every stage of ``date`` finished.

    This is the completion contract: a month may only be stamped as processed
    once this holds. A run that failed part way leaves it false, so the next run
    retries instead of treating a partial month as finished.
    """
    return not stages_not_completed(date)


def get_run_for_date() -> str | None:
    last = settings.state_file
    if last.exists():
        return last.read_text().strip()
    return None


def print_status(date: str | None, return_map: bool = False):
    d = _read()
    if date is None:
        if not d:
            logger.info("Sem execuções registradas.")
            return None
        for k, v in d.items():
            logger.info(f"Data: {k} -> {v}")
        return None
    m = d.get(date, None)
    if m is None:
        logger.info("Execução não encontrada.")
        return None
    logger.info(f"Status {date}: {m}")
    if return_map:
        return m
    return None


StepStatus = Literal["pending", "running", "completed", "failed", "partial"]


class PipelineState:
    def __init__(self, date: Optional[str] = None):
        if date is None:
            date = get_run_for_date()
        self.date = date or "session"
        start_run(self.date)

    def should_skip(self, step: str) -> bool:
        d = _read()
        m = d.get(self.date, {})
        return m.get(step, "pending") == "completed"

    def update(self, step: str, status: StepStatus) -> None:
        mark_stage(self.date, step, status)


state = PipelineState()
