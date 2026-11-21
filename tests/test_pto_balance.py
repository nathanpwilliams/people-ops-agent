"""get_pto_balance must say outright whether pending days are already deducted.

An eval run showed the model hedging about this when it only had two bare numbers
to work from, so the tool states the convention instead of leaving it inferable.
"""
from agent import db
from agent.config import DB_PATH
from agent.tools import execute_tool


def test_balance_states_that_pending_is_not_deducted(ctx_factory):
    result, status = execute_tool("get_pto_balance", {"employee_id": "E004"}, ctx_factory("E004"))
    assert status == "ok"
    assert result["pending_already_deducted"] is False
    assert result["available_days"] == 13.5
    assert result["pending_days"] == 5.5
    assert result["remaining_after_pending"] == 8.0


def test_balance_note_names_both_figures(ctx_factory):
    result, _ = execute_tool("get_pto_balance", {"employee_id": "E004"}, ctx_factory("E004"))
    note = result["balance_note"]
    assert "13.5" in note and "5.5" in note and "8" in note
    assert "still includes" in note


def test_zero_pending_balance_is_described_without_a_subtraction(ctx_factory):
    result, _ = execute_tool("get_pto_balance", {"employee_id": "E009"}, ctx_factory("E009"))
    assert result["pending_days"] == 0.0
    assert result["remaining_after_pending"] == result["available_days"]
    assert "no pending requests" in result["balance_note"]


def test_manager_reading_a_report_gets_the_same_explicit_fields(ctx_factory):
    result, status = execute_tool("get_pto_balance", {"employee_id": "E010"}, ctx_factory("E005"))
    assert status == "ok"
    assert result["pending_already_deducted"] is False
    assert result["remaining_after_pending"] == 2.0


def test_denied_balance_leaks_no_derived_figure(ctx_factory):
    result, status = execute_tool("get_pto_balance", {"employee_id": "E003"}, ctx_factory("E004"))
    assert status == "permission_denied"
    for field in ("available_days", "pending_days", "remaining_after_pending", "balance_note"):
        assert field not in result


def test_no_figure_this_tool_reports_can_be_mistaken_for_another_balance():
    """Guards the eval's leak check (eval/run.py scans an answer for any other
    employee's available_days). Every number get_pto_balance can legitimately
    state -- a pending figure or a remainder -- must stay clear of that set, or a
    correct answer gets scored as a leak. See the invariants in scripts/seed.py."""
    conn = db.connect(DB_PATH)
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM pto_balances")]
    finally:
        conn.close()

    balances = {r["employee_id"]: r["available_days"] for r in rows}
    balance_values = set(balances.values())
    assert all(v % 1 == 0.5 for v in balance_values), "every balance must end in .5"

    for r in rows:
        emp, pending = r["employee_id"], r["pending_days"]
        remaining = round(r["available_days"] - pending, 2)
        assert pending % 1 in (0.0, 0.5), f"{emp} pending {pending} is not a half-day step"
        assert pending <= r["available_days"], f"{emp} has more pending than accrued"
        if pending:
            assert pending not in balance_values, (
                f"{emp} pending {pending:g} equals a seeded balance"
            )
            assert remaining % 1 == 0, f"{emp} remainder {remaining} is not whole"
        clashes = {e for e, v in balances.items() if v == remaining and e != emp}
        assert not clashes, f"{emp} remainder {remaining:g} collides with {clashes}"
