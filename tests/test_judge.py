"""The LLM-as-judge grader, exercised offline against the fake client.

The judge's own output is what these tests pin down: a clear answer passes, a hedged one
fails, a non-PTO answer is not applicable, and anything malformed comes back as "error"
rather than an exception. The last test guards the property that matters most -- the judge
cannot see what the answer was expected to contain.
"""
import json

from eval.judge import (
    RUBRIC,
    VERDICT_TOOL,
    VERDICTS,
    build_prompt,
    judge,
    judge_all,
)
from tests.conftest import FakeClient, text_message, tool_use_message

CLEAR_ANSWER = (
    "You have 8 days of PTO left to book.\n"
    "- Total balance: 13.5 days. This still includes your pending requests.\n"
    "- Pending: 5.5 days you've requested but haven't taken yet.\n"
    "- Left to book: 13.5 - 5.5 = 8 days."
)
HEDGED_ANSWER = (
    "You have 13.5 days available and 5.5 days pending. The 13.5 may already account for "
    "the pending days, so you may want to confirm in the HR portal before booking."
)
POLICY_ANSWER = "Up to 5 unused PTO days carry over into the next calendar year (PTO §2.2)."


def verdict_client(verdict: str, reason: str = "because") -> FakeClient:
    """A fake judge that returns one verdict through the record_verdict tool."""
    return FakeClient(
        [lambda n: tool_use_message(n, VERDICT_TOOL["name"], {"verdict": verdict, "reason": reason})]
    )


def test_a_clear_answer_passes():
    client = verdict_client("pass", "States all three figures and which is bookable.")
    result = judge("How much PTO do I have left?", CLEAR_ANSWER, client)

    assert result == {
        "item_id": "pto_figures_unambiguous",
        "verdict": "pass",
        "reason": "States all three figures and which is bookable.",
    }


def test_a_hedged_answer_fails():
    client = verdict_client("fail", "Hedges with 'may already account for'.")
    result = judge("How much PTO do I have left?", HEDGED_ANSWER, client)

    assert result["verdict"] == "fail"
    assert result["item_id"] == "pto_figures_unambiguous"
    assert "may already account for" in result["reason"]


def test_an_answer_with_no_pto_figures_is_not_applicable():
    client = verdict_client("not_applicable", "Policy answer, reports no balance.")
    result = judge("Can I carry days over?", POLICY_ANSWER, client)

    assert result["verdict"] == "not_applicable"


def test_a_text_only_response_is_an_error_not_an_exception():
    client = FakeClient([lambda n: text_message(n, "Looks fine to me!")])
    result = judge("How much PTO do I have?", CLEAR_ANSWER, client)

    assert result["verdict"] == "error"
    assert result["item_id"] == "pto_figures_unambiguous"
    assert "no record_verdict call" in result["reason"]
    assert "Looks fine to me!" in result["reason"]


def test_an_unknown_verdict_value_is_an_error():
    client = verdict_client("probably fine")
    result = judge("How much PTO do I have?", CLEAR_ANSWER, client)

    assert result["verdict"] == "error"
    assert "not one of" in result["reason"]


def test_a_missing_verdict_field_is_an_error():
    client = FakeClient(
        [lambda n: tool_use_message(n, VERDICT_TOOL["name"], {"reason": "forgot the verdict"})]
    )
    result = judge("How much PTO do I have?", CLEAR_ANSWER, client)

    assert result["verdict"] == "error"
    assert "not one of" in result["reason"]


def test_a_raising_client_is_an_error():
    def explode(_n):
        raise RuntimeError("judge API is down")

    result = judge("How much PTO do I have?", CLEAR_ANSWER, FakeClient([explode]))

    assert result["verdict"] == "error"
    assert "judge API is down" in result["reason"]


def test_the_judge_never_sees_the_expectation_or_the_case_id():
    """A judge handed `answer_contains` would agree with the lexical check by
    construction. It gets the question and the answer, and nothing else."""
    row = {
        "id": "own_pto_balance",
        "question": "How much PTO do I have left?",
        "expect": {"answer_contains": ["13.5", "SENTINEL_EXPECTED_STRING"]},
        "answer": CLEAR_ANSWER,
    }
    client = verdict_client("pass")

    judge(row["question"], row["answer"], client)

    sent = json.dumps(client.requests[0], default=str)
    assert "SENTINEL_EXPECTED_STRING" not in sent
    assert "own_pto_balance" not in sent
    assert "answer_contains" not in sent
    assert "expect" not in sent
    # The things it *should* see did arrive.
    assert "How much PTO do I have left?" in sent
    assert "Left to book" in sent


def test_the_request_forces_a_machine_parseable_verdict():
    client = verdict_client("pass")
    judge("q", "a", client, model="judge-model-x")
    sent = client.requests[0]

    assert sent["model"] == "judge-model-x"
    # Forced tool use is rejected by claude-opus-5-5, so the verdict is kept
    # machine-parseable with auto + a strict schema + no parallel calls.
    assert sent["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert sent["tools"][0]["strict"] is True
    assert sent["tools"][0]["input_schema"]["additionalProperties"] is False
    assert sent["tools"][0]["input_schema"]["properties"]["verdict"]["enum"] == list(VERDICTS)


def test_the_prompt_carries_the_rubric_criteria():
    item = RUBRIC[0]
    prompt = build_prompt("How much PTO do I have?", CLEAR_ANSWER, item)

    assert item["id"] in prompt
    assert item["pass_criteria"] in prompt
    assert item["fail_criteria"] in prompt
    assert item["not_applicable_criteria"] in prompt


def test_judge_all_returns_one_result_per_rubric_item():
    client = verdict_client("pass")
    results = judge_all("How much PTO do I have?", CLEAR_ANSWER, client)

    assert [r["item_id"] for r in results] == [item["id"] for item in RUBRIC]
    assert client.calls == len(RUBRIC)


def test_rubric_items_are_well_formed():
    seen = set()
    for item in RUBRIC:
        assert item["id"] not in seen, f"duplicate rubric id {item['id']}"
        seen.add(item["id"])
        for field in ("id", "question", "pass_criteria", "fail_criteria", "not_applicable_criteria"):
            assert item.get(field), f"{item['id']} is missing {field}"
        assert item["question"].endswith("?"), f"{item['id']} question should be a question"
