"""Paths and model settings. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

HANDBOOK_DIR = Path(os.environ.get("PEOPLE_OPS_HANDBOOK_DIR", ROOT / "data" / "handbook"))
DB_PATH = Path(os.environ.get("PEOPLE_OPS_DB_PATH", ROOT / "data" / "company.db"))
LOGS_DIR = Path(os.environ.get("PEOPLE_OPS_LOGS_DIR", ROOT / "logs"))
OUTBOX_DIR = Path(os.environ.get("PEOPLE_OPS_OUTBOX_DIR", ROOT / "outbox"))

MODEL = os.environ.get("PEOPLE_OPS_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("PEOPLE_OPS_EFFORT", "medium")
MAX_TOKENS = 16000
MAX_STEPS = 8
