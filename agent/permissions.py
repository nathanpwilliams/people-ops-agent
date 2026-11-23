"""Authorization rules. These run in code, after the model has decided what to call.

The model never sees or sets `requesting_user_id`; the application injects it. So even
if the model is talked into asking for someone else's data, the check here refuses.
"""
from __future__ import annotations

from pathlib import Path

from . import db
from .config import DB_PATH


def can_view_pto(requesting_user_id: str, target_employee_id: str, db_path: Path = DB_PATH) -> bool:
    """A user may see their own balance, or the balance of a direct report."""
    if requesting_user_id == target_employee_id:
        return True
    target = db.get_employee(target_employee_id, db_path)
    return target is not None and target["manager_id"] == requesting_user_id


def can_create_onboarding_checklist(
    requesting_user_id: str, target_employee_id: str, db_path: Path = DB_PATH
) -> bool:
    """Only the new hire's own manager may create their checklist.

    Deliberately narrower than `can_view_pto`: there is no self case (you do not onboard
    yourself) and no skip-level case (a manager two levels up is not the one doing it).
    """
    if requesting_user_id == target_employee_id:
        return False
    target = db.get_employee(target_employee_id, db_path)
    return target is not None and target["manager_id"] == requesting_user_id


def visible_employee_ids(requesting_user_id: str, db_path: Path = DB_PATH) -> set[str]:
    """Every employee whose PTO the requesting user is allowed to see."""
    ids = {requesting_user_id}
    ids.update(e["id"] for e in db.direct_reports(requesting_user_id, db_path))
    return ids
