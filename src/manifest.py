"""Run manifest: what this run expected to download, and what it produced.

A stage can lose work and the next stage cannot tell. A corrupt archive is
logged and skipped, a changed directory listing yields nothing, and the month
gets stamped as processed either way. Counting files does not help: the Receita
ships a different number of volumes every month, so any expected count is wrong
within a month.

The manifest records what *this* run observed, so the expectation self-adapts.
It is written to a temporary file and renamed, so an interrupted download leaves
no partial manifest -- and the absence of one is itself a signal rather than an
empty expectation.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"


def manifest_path(base: Path) -> Path:
    return base / MANIFEST_NAME


def write_manifest(base: Path, payload: dict[str, Any]) -> Path:
    """Write the manifest atomically."""
    path = manifest_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    logger.debug("manifesto gravado em %s", path)
    return path


def read_manifest(base: Path) -> dict[str, Any] | None:
    """The manifest, or None when there is none or it is unreadable."""
    path = manifest_path(base)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Manifesto ilegível em %s; tratando como ausente.", path)
        return None
    if not isinstance(data, dict) or "tables" not in data:
        logger.warning("Manifesto em %s sem a chave 'tables'; tratando como ausente.", path)
        return None
    return data


def expected_archives(manifest: dict[str, Any]) -> dict[str, list[str]]:
    """Archive names per table, as the download recorded them."""
    tables = manifest.get("tables") or {}
    out: dict[str, list[str]] = {}
    for table, entries in tables.items():
        if isinstance(entries, list):
            names = [
                str(e.get("name"))
                for e in entries
                if isinstance(e, dict) and e.get("name")
            ]
            if names:
                out[str(table)] = sorted(names)
    return out