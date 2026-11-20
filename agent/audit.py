"""Append-only JSONL audit log. One record per agent turn."""
from __future__ import annotations

import json
from pathlib import Path

from .config import LOGS_DIR


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else Path(LOGS_DIR) / "audit.jsonl"

    def write(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
