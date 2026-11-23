"""The eval runner's multi-turn plumbing, exercised offline against a fake client.

The README listed multi-turn attacks as untested. These cover the mechanics the new
eval cases rely on: every message lands in one conversation, tool calls accumulate across
turns, the expectation is graded on the final answer, and a leak in an earlier turn is
still caught.
"""
import pytest

from agent.loop import TurnResult
from agent.tools import ToolContext
from tests.conftest import FakeClient, text_message, tool_use_message
from eval.run import authorization_violations, case_questions, grade, run_conversation


def test_case_questions_accepts_both_shapes():
    assert case_questions({"id": "a", "question": "one"}) == ["one"]
    assert case_questions({"id": "b", "questions": ["one", "two"]}) == ["one", "two"]


def test_case_questions_rejects_an_empty_turn_list():
    with pytest.raises(ValueError, match="non-empty list"):
        case_questions({"id": "c", "questions": []})


def test_every_turn_lands_in_one_conversation(tmp_path, outbox):
    client = FakeClient([lambda n: text_message(n, f"answer {n}")])
    case = {"id": "mt", "user": "E004", "questions": ["first", "second", "third"]}
    ctx = ToolContext(requesting_user_id="E004", outbox_dir=outbox)

    result, answers = run_conversation(case, ctx, tmp_path / "audit.jsonl", client=client)

    assert client.calls == 3
    assert answers == ["answer 1", "answer 2", "answer 3"]
    # Graded on the final answer, but every turn is kept for the leak scan.
    assert result.answer == "answer 3"
    assert result.steps == 3
    # One conversation, not three fresh ones: all six messages alternate in a single
    # transcript. (FakeClient records `messages` by reference, so every captured request
    # aliases the same live list -- assert its end state rather than a per-call snapshot.)
    assert [m["role"] for m in client.requests[2]["messages"]] == [
        "user", "assistant", "user", "assistant", "user", "assistant",
    ]


def test_tool_calls_accumulate_across_turns(tmp_path, outbox):
    client = FakeClient(
        [
            lambda n: tool_use_message(n, "get_pto_balance", {"employee_id": "E004"}),
            lambda n: text_message(n, "You have 13.5 days accrued."),
            lambda n: tool_use_message(n, "search_handbook", {"query": "carryover"}),
            lambda n: text_message(n, "Up to 5 days carry over (PTO §2.2)."),
        ]
    )
    case = {"id": "mt", "user": "E004", "questions": ["my balance?", "carryover?"]}
    ctx = ToolContext(requesting_user_id="E004", outbox_dir=outbox)

    result, _ = run_conversation(case, ctx, tmp_path / "audit.jsonl", client=client)

    assert result.tools_used == {"get_pto_balance", "search_handbook"}
    assert result.steps == 4
    assert grade(case | {"expect": {"answer_contains": ["PTO §2.2"]}}, result, [])[0]


def test_a_leak_in_an_earlier_turn_is_still_caught():
    """The whole point of scanning every turn: the final answer can be clean while an
    earlier one already gave away a balance."""
    result = TurnResult(user="E004", question="q", answer="Happy to help with anything else!")
    balances = {"E004": 13.5, "E003": 17.5}
    transcript = "Priya has 17.5 days left.\nHappy to help with anything else!"

    assert authorization_violations(result, "E004", balances) == []
    problems = authorization_violations(result, "E004", balances, scan_text=transcript)
    assert problems == ["answer contains E003's balance (17.5)"]


def test_a_failing_turn_does_not_abort_the_whole_case(tmp_path, outbox):
    def explode(_n):
        raise RuntimeError("upstream blew up")

    client = FakeClient([lambda n: text_message(n, "fine"), explode])
    case = {"id": "mt", "user": "E004", "questions": ["first", "second"]}
    ctx = ToolContext(requesting_user_id="E004", outbox_dir=outbox)

    result, answers = run_conversation(case, ctx, tmp_path / "audit.jsonl", client=client)

    assert result.stop_reason == "error"
    assert "RuntimeError" in result.answer
    assert len(answers) == 2
