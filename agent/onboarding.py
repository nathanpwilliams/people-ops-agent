"""The standard onboarding checklist from the handbook, as data.

Handbook Onboarding §6 lists the standard checklist every manager uses, and §1, §3 and §5
give the deadlines. The offsets below encode those deadlines relative to the new hire's
start date, so a generated checklist and the handbook cannot drift apart silently.

Business-day offsets count weekends but not company holidays -- Holidays §1 lists 11 a
year, and a real implementation should skip them too.
"""
from __future__ import annotations

from datetime import date, timedelta

# (task, owner, offset, unit) -- offset is relative to the start date.
# "business" counts Mon-Fri only; "calendar" counts every day.
STANDARD_TASKS = (
    ("New-hire paperwork complete", "People Ops", -3, "business"),   # §1 before first day
    ("Laptop shipped", "IT", -1, "business"),                        # §1 arrives before start
    ("Accounts created", "IT", 0, "calendar"),                       # §2 day one
    ("Buddy assigned", "Manager", 0, "calendar"),                    # §1, §4
    ("First-week schedule sent", "Manager", 0, "calendar"),          # §1
    ("30-60-90 plan agreed", "Manager", 5, "business"),              # §3 first week
    ("Required training complete", "Employee", 10, "business"),      # §3, §6 10 business days
    ("Benefits enrolled", "Employee", 30, "calendar"),               # §6 within 30 days
    ("90-day check-in scheduled", "Manager", 90, "calendar"),        # §5
)


def add_business_days(start: date, n: int) -> date:
    """Move `n` business days from `start` (negative moves backwards)."""
    if n == 0:
        return start
    step = 1 if n > 0 else -1
    remaining = abs(n)
    current = start
    while remaining:
        current += timedelta(days=step)
        if current.weekday() < 5:
            remaining -= 1
    return current


def due_date(start: date, offset: int, unit: str) -> date:
    if unit == "business":
        return add_business_days(start, offset)
    if unit == "calendar":
        return start + timedelta(days=offset)
    raise ValueError(f"unknown offset unit {unit!r}")


def standard_checklist(start: date) -> list[dict]:
    """The standard tasks with concrete due dates, ordered by when they fall due."""
    tasks = [
        {
            "task": task,
            "owner": owner,
            "due_date": due_date(start, offset, unit).isoformat(),
            "status": "open",
        }
        for task, owner, offset, unit in STANDARD_TASKS
    ]
    return sorted(tasks, key=lambda t: (t["due_date"], t["task"]))
