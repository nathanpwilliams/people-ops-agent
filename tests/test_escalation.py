import json
import re

from agent.tools import ESCALATION_CATEGORIES, execute_tool


def test_escalation_writes_well_formed_record(ctx_factory, outbox):
    result, status = execute_tool(
        "escalate_to_human",
        {"category": "compensation", "summary": "Employee asked about a raise."},
        ctx_factory("E006"),
    )
    assert status == "ok"
    assert re.fullmatch(r"ESC-\d{8}-[0-9A-F]{6}", result["ticket_id"])

    lines = (outbox / "escalations.jsonl").read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record == {
        "ticket_id": result["ticket_id"],
        "timestamp": record["timestamp"],
        "requesting_user_id": "E006",
        "category": "compensation",
        "summary": "Employee asked about a raise.",
        "status": "open",
    }
    assert record["timestamp"].endswith("+00:00")


def test_escalations_append(ctx_factory, outbox):
    for category in ESCALATION_CATEGORIES:
        _, status = execute_tool("escalate_to_human", {"category": category, "summary": "x"}, ctx_factory("E004"))
        assert status == "ok"
    assert len((outbox / "escalations.jsonl").read_text().splitlines()) == len(ESCALATION_CATEGORIES)


def test_invalid_category_is_structured_error(ctx_factory, outbox):
    result, status = execute_tool("escalate_to_human", {"category": "gossip", "summary": "x"}, ctx_factory("E004"))
    assert status == "invalid_input"
    assert result["error"] == "invalid_input"
    assert not (outbox / "escalations.jsonl").exists()
