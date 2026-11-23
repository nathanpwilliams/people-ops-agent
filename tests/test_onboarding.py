"""create_onboarding_checklist: who may call it, and the dry-run/confirm contract.

Every test here runs against a copy of the database (`rw_ctx_factory`), so a confirmed
create can be asserted without touching the committed data/company.db.

E005 Sofia manages E010 Diego. E011 Noor already has a seeded checklist.
"""
from datetime import date

from agent import db
from agent.onboarding import STANDARD_TASKS, add_business_days, standard_checklist
from agent.permissions import can_create_onboarding_checklist
from agent.tools import execute_tool

ARGS = {"employee_id": "E010", "start_date": "2026-12-01", "confirm": False}


def test_manager_gets_a_preview_that_writes_nothing(rw_ctx_factory):
    ctx = rw_ctx_factory("E005")
    result, status = execute_tool("create_onboarding_checklist", dict(ARGS), ctx)

    assert status == "ok"
    assert result["dry_run"] is True
    assert result["task_count"] == len(STANDARD_TASKS)
    assert "created_task_ids" not in result
    assert db.onboarding_tasks("E010", ctx.db_path) == []


def test_confirmed_create_inserts_tasks_and_returns_ids(rw_ctx_factory):
    ctx = rw_ctx_factory("E005")
    result, status = execute_tool(
        "create_onboarding_checklist", ARGS | {"confirm": True}, ctx
    )

    assert status == "ok"
    assert result["dry_run"] is False
    assert len(result["created_task_ids"]) == len(STANDARD_TASKS)

    rows = db.onboarding_tasks("E010", ctx.db_path)
    assert len(rows) == len(STANDARD_TASKS)
    assert {r["task"] for r in rows} == {t[0] for t in STANDARD_TASKS}
    assert all(r["status"] == "open" for r in rows)
    assert all(r["employee_id"] == "E010" for r in rows)


def test_non_manager_is_denied(rw_ctx_factory):
    # E004 is a peer of E010, not their manager.
    ctx = rw_ctx_factory("E004")
    result, status = execute_tool(
        "create_onboarding_checklist", ARGS | {"confirm": True}, ctx
    )

    assert status == "permission_denied"
    assert "tasks" not in result
    assert db.onboarding_tasks("E010", ctx.db_path) == []


def test_skip_level_manager_is_denied(rw_ctx_factory):
    # E010 reports to E005, who reports to E002. E002 is not the direct manager.
    ctx = rw_ctx_factory("E002")
    _, status = execute_tool("create_onboarding_checklist", ARGS | {"confirm": True}, ctx)

    assert status == "permission_denied"
    assert db.onboarding_tasks("E010", ctx.db_path) == []


def test_nobody_onboards_themselves(rw_ctx_factory):
    ctx = rw_ctx_factory("E010")
    _, status = execute_tool(
        "create_onboarding_checklist",
        {"employee_id": "E010", "start_date": "2026-12-01", "confirm": True},
        ctx,
    )

    assert status == "permission_denied"
    assert db.onboarding_tasks("E010", ctx.db_path) == []


def test_duplicate_checklist_is_rejected(rw_ctx_factory):
    # E011 already has a seeded checklist; E006 is their manager.
    ctx = rw_ctx_factory("E006")
    before = db.onboarding_tasks("E011", ctx.db_path)
    assert before, "fixture expects E011 to start with a checklist"

    result, status = execute_tool(
        "create_onboarding_checklist",
        {"employee_id": "E011", "start_date": "2026-12-01", "confirm": True},
        ctx,
    )

    assert status == "checklist_exists"
    assert result["existing_task_count"] == len(before)
    assert db.onboarding_tasks("E011", ctx.db_path) == before


def test_duplicate_is_rejected_on_the_dry_run_too(rw_ctx_factory):
    ctx = rw_ctx_factory("E006")
    _, status = execute_tool(
        "create_onboarding_checklist",
        {"employee_id": "E011", "start_date": "2026-12-01", "confirm": False},
        ctx,
    )
    assert status == "checklist_exists"


def test_a_second_confirmed_call_cannot_double_up(rw_ctx_factory):
    ctx = rw_ctx_factory("E005")
    execute_tool("create_onboarding_checklist", ARGS | {"confirm": True}, ctx)
    _, status = execute_tool("create_onboarding_checklist", ARGS | {"confirm": True}, ctx)

    assert status == "checklist_exists"
    assert len(db.onboarding_tasks("E010", ctx.db_path)) == len(STANDARD_TASKS)


def test_a_bad_date_is_rejected_before_anything_is_written(rw_ctx_factory):
    ctx = rw_ctx_factory("E005")
    for bad in ["01/12/2026", "2026-13-01", "next Monday", "", "2026-02-30"]:
        result, status = execute_tool(
            "create_onboarding_checklist",
            {"employee_id": "E010", "start_date": bad, "confirm": True},
            ctx,
        )
        assert status == "invalid_input", f"{bad!r} should be rejected"
        assert "must be an ISO date" in result["message"]
    assert db.onboarding_tasks("E010", ctx.db_path) == []


def test_an_unauthorized_caller_learns_nothing_about_an_existing_checklist(rw_ctx_factory):
    """Permission is checked before the duplicate check, so the error cannot be used to
    probe whether a checklist exists for someone you do not manage."""
    ctx = rw_ctx_factory("E004")
    result, status = execute_tool(
        "create_onboarding_checklist",
        {"employee_id": "E011", "start_date": "2026-12-01", "confirm": False},
        ctx,
    )
    assert status == "permission_denied"
    assert "existing_task_count" not in result


def test_due_dates_follow_the_handbook_deadlines():
    # 2026-12-01 is a Tuesday.
    start = date(2026, 12, 1)
    tasks = {t["task"]: t["due_date"] for t in standard_checklist(start)}

    assert tasks["Accounts created"] == "2026-12-01"
    # Onboarding §3/§6: required training within 10 business days.
    assert tasks["Required training complete"] == add_business_days(start, 10).isoformat()
    assert tasks["Required training complete"] == "2026-12-15"
    # §6: benefits within 30 calendar days. §5: check-in at 90 days.
    assert tasks["Benefits enrolled"] == "2026-12-31"
    assert tasks["90-day check-in scheduled"] == "2027-03-01"
    # Paperwork and laptop fall before the start date.
    assert tasks["New-hire paperwork complete"] < start.isoformat()
    assert tasks["Laptop shipped"] < start.isoformat()


def test_business_days_skip_weekends():
    friday = date(2026, 12, 4)
    assert add_business_days(friday, 1) == date(2026, 12, 7)   # -> Monday
    assert add_business_days(friday, 0) == friday
    monday = date(2026, 12, 7)
    assert add_business_days(monday, -1) == friday


def test_checklist_is_ordered_by_due_date():
    dates = [t["due_date"] for t in standard_checklist(date(2026, 12, 1))]
    assert dates == sorted(dates)


def test_permission_rule_is_direct_manager_only():
    assert can_create_onboarding_checklist("E005", "E010")
    assert not can_create_onboarding_checklist("E002", "E010")   # skip level
    assert not can_create_onboarding_checklist("E010", "E010")   # self
    assert not can_create_onboarding_checklist("E004", "E010")   # peer
    assert not can_create_onboarding_checklist("E005", "E999")   # unknown employee
