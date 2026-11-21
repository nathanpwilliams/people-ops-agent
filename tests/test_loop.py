import json

from agent.loop import Agent
from tests.conftest import FakeClient, text_message, tool_use_message


def test_loop_stops_at_step_cap_and_escalates(ctx_factory, audit, outbox):
    client = FakeClient([lambda n: tool_use_message(n, "search_handbook", {"query": "pto"})])
    agent = Agent("E004", client=client, tool_ctx=ctx_factory("E004"), audit=audit, max_steps=8)

    result = agent.run("Keep searching forever")

    assert client.calls == 8
    assert result.steps == 8
    assert result.stopped_at_cap is True
    assert result.escalation is not None
    assert result.escalation["ticket_id"] in result.answer
    assert "couldn't complete" in result.answer.lower() or "wasn't able" in result.answer.lower()
    # The escalation was filed in code, with the step-cap category.
    record = json.loads((outbox / "escalations.jsonl").read_text().splitlines()[-1])
    assert record["category"] == "other"
    # Transcript ends with an assistant message so a follow-up turn is well-formed.
    assert agent.messages[-1]["role"] == "assistant"


def test_loop_returns_text_and_records_audit(ctx_factory, audit):
    client = FakeClient(
        [
            lambda n: tool_use_message(n, "get_pto_balance", {"employee_id": "E004"}),
            lambda n: text_message(n, "You have 13.5 days accrued (5.5 pending), so 8 left to book."),
        ]
    )
    agent = Agent("E004", client=client, tool_ctx=ctx_factory("E004"), audit=audit)

    result = agent.run("How much PTO do I have?")

    assert result.answer.startswith("You have 13.5")
    assert result.steps == 2
    assert [c.status for c in result.tool_calls] == ["ok"]
    assert result.input_tokens == 200 and result.output_tokens == 40

    records = audit.read_all()
    assert len(records) == 1
    rec = records[0]
    assert rec["user"] == "E004"
    assert rec["question"] == "How much PTO do I have?"
    assert rec["tool_calls"] == [{"name": "get_pto_balance", "args": {"employee_id": "E004"}, "status": "ok"}]
    assert rec["final_answer"] == result.answer
    assert rec["steps"] == 2
    assert rec["input_tokens"] == 200
    assert rec["latency_ms"] >= 0

    # The tool result went back to the model as structured JSON in a single user message.
    tool_result_msg = client.requests[1]["messages"][2]
    assert tool_result_msg["role"] == "user"
    payload = json.loads(tool_result_msg["content"][0]["content"])
    assert payload["available_days"] == 13.5
    assert payload["pending_already_deducted"] is False
    assert payload["remaining_after_pending"] == 8.0


def test_identity_comes_from_app_not_model(ctx_factory, audit):
    # The model tries to read someone else's balance; the tool context says the user is E004.
    client = FakeClient(
        [
            lambda n: tool_use_message(n, "get_pto_balance", {"employee_id": "E003"}),
            lambda n: text_message(n, "I can't share another employee's balance."),
        ]
    )
    agent = Agent("E004", client=client, tool_ctx=ctx_factory("E004"), audit=audit)

    result = agent.run("What's Priya's balance?")

    assert result.tool_calls[0].status == "permission_denied"
    payload = json.loads(client.requests[1]["messages"][2]["content"][0]["content"])
    assert payload["error"] == "permission_denied"
    assert "available_days" not in payload


def test_tool_errors_do_not_crash_the_loop(ctx_factory, audit):
    client = FakeClient(
        [
            lambda n: tool_use_message(n, "no_such_tool", {}),
            lambda n: tool_use_message(n, "escalate_to_human", {"category": "bogus", "summary": "x"}),
            lambda n: text_message(n, "done"),
        ]
    )
    agent = Agent("E004", client=client, tool_ctx=ctx_factory("E004"), audit=audit)

    result = agent.run("break things")

    assert result.answer == "done"
    assert [c.status for c in result.tool_calls] == ["unknown_tool", "invalid_input"]
    assert client.requests[1]["messages"][2]["content"][0]["is_error"] is True


def test_system_prompt_pins_identity(ctx_factory, audit):
    client = FakeClient([lambda n: text_message(n, "hi")])
    agent = Agent("E002", client=client, tool_ctx=ctx_factory("E002"), audit=audit)
    agent.run("hello")
    system = client.requests[0]["system"]
    assert "Employee id: E002" in system
    assert "Jordan Alvarez (E004)" in system  # listed as a direct report
    assert client.requests[0]["tools"][0]["strict"] is True
