"""Thin helpers over the SQLite company database.

Reads are unrestricted; the only write is `insert_onboarding_tasks`, which the
onboarding tool calls after its permission and duplicate checks have passed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import DB_PATH


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_employee(employee_id: str, db_path: Path = DB_PATH) -> dict | None:
    rows = _rows(db_path, "SELECT * FROM employees WHERE id = ?", (employee_id,))
    return rows[0] if rows else None


def list_employees(db_path: Path = DB_PATH) -> list[dict]:
    return _rows(db_path, "SELECT * FROM employees ORDER BY id")


def direct_reports(manager_id: str, db_path: Path = DB_PATH) -> list[dict]:
    return _rows(db_path, "SELECT * FROM employees WHERE manager_id = ? ORDER BY id", (manager_id,))


def get_pto_balance(employee_id: str, db_path: Path = DB_PATH) -> dict | None:
    rows = _rows(
        db_path,
        "SELECT employee_id, available_days, pending_days FROM pto_balances WHERE employee_id = ?",
        (employee_id,),
    )
    return rows[0] if rows else None


def onboarding_tasks(employee_id: str, db_path: Path = DB_PATH) -> list[dict]:
    return _rows(
        db_path,
        "SELECT * FROM onboarding_tasks WHERE employee_id = ? ORDER BY due_date, id",
        (employee_id,),
    )


def insert_onboarding_tasks(employee_id: str, tasks: list[dict], db_path: Path = DB_PATH) -> list[int]:
    """Insert tasks for one employee in a single transaction and return the new row ids."""
    conn = connect(db_path)
    try:
        with conn:
            ids = [
                conn.execute(
                    "INSERT INTO onboarding_tasks (employee_id, task, owner, due_date, status)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (employee_id, t["task"], t["owner"], t["due_date"], t.get("status", "open")),
                ).lastrowid
                for t in tasks
            ]
        return ids
    finally:
        conn.close()
