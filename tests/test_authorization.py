from agent.permissions import can_view_pto, visible_employee_ids
from agent.tools import execute_tool


def test_non_manager_cannot_read_peer_balance(ctx_factory):
    result, status = execute_tool("get_pto_balance", {"employee_id": "E003"}, ctx_factory("E004"))
    assert status == "permission_denied"
    assert result["error"] == "permission_denied"
    assert "available_days" not in result


def test_employee_can_read_own_balance(ctx_factory):
    result, status = execute_tool("get_pto_balance", {"employee_id": "E004"}, ctx_factory("E004"))
    assert status == "ok"
    assert result["available_days"] == 13.5


def test_manager_can_read_direct_report(ctx_factory):
    result, status = execute_tool("get_pto_balance", {"employee_id": "E004"}, ctx_factory("E002"))
    assert status == "ok"
    assert result["employee_id"] == "E004"


def test_manager_cannot_read_skip_level_report(ctx_factory):
    # E010 reports to E005, who reports to E002. E002 is not E010's direct manager.
    _, status = execute_tool("get_pto_balance", {"employee_id": "E010"}, ctx_factory("E002"))
    assert status == "permission_denied"


def test_manager_cannot_read_non_report(ctx_factory):
    _, status = execute_tool("get_pto_balance", {"employee_id": "E003"}, ctx_factory("E005"))
    assert status == "permission_denied"


def test_unknown_employee_is_denied_not_crashed(ctx_factory):
    result, status = execute_tool("get_pto_balance", {"employee_id": "E999"}, ctx_factory("E001"))
    assert status == "permission_denied"
    assert result["error"] == "permission_denied"


def test_model_cannot_override_identity_via_arguments(ctx_factory):
    # Extra arguments (which the strict schema would reject anyway) never change who is asking.
    result, status = execute_tool(
        "get_pto_balance", {"employee_id": "E003", "requesting_user_id": "E001"}, ctx_factory("E004")
    )
    assert status == "invalid_input"
    assert "available_days" not in result


def test_visible_ids_are_self_plus_direct_reports():
    assert visible_employee_ids("E004") == {"E004"}
    assert visible_employee_ids("E005") == {"E005", "E010"}
    assert can_view_pto("E002", "E005")
    assert not can_view_pto("E005", "E002")
