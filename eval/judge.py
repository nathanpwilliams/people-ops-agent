"""LLM-as-judge grading for answer quality.

Lexical grading in eval/run.py checks that required strings are present. It cannot tell a
clear answer from a muddled one that happens to contain the same substring -- the case
that motivated this module is `own_pto_balance`, which asserts the answer contains "13.5"
and so passed equally before and after the pending-days fix, hedging and all.

Two deliberate limits:

* **The judge never sees the expectation or the case id.** It gets the user's question and
  the agent's answer, nothing else. Handing it `answer_contains` would let it grade toward
  the expected result and agree with the lexical check by construction, which is the one
  thing a second opinion must not do.
* **This grades clarity, not correctness.** Whether the stated convention matches
  Northwind's (pending is *not* deducted) is a separate question from whether the answer
  states a convention at all. A rubric item for correctness belongs here later; it would
  need the ground truth this one is careful not to have.

Not wired into eval/run.py yet. `judge()` never raises -- a malformed response comes back
as `verdict: "error"` so one bad grade cannot take down a run.
"""
from __future__ import annotations

import json
from typing import Any

from agent.config import JUDGE_MODEL

VERDICTS = ("pass", "fail", "not_applicable")

RUBRIC = [
    {
        "id": "pto_figures_unambiguous",
        "question": (
            "If this answer reports PTO figures, does it state each figure plainly and say "
            "which is which, without hedging about whether pending days are deducted?"
        ),
        "pass_criteria": (
            "The answer reports PTO figures and a reader can tell, without guessing, what "
            "each number means: the accrued or available balance, the pending days already "
            "requested but not yet taken, and how many days are actually left to book. The "
            "relationship between them is stated rather than implied -- in particular "
            "whether the pending days are still inside the available balance or already "
            "taken out of it. The answer commits to one reading."
        ),
        "fail_criteria": (
            "The answer reports PTO figures but leaves a reader unsure what a number means "
            "or how the figures relate. This includes: hedging about whether pending days "
            "have been deducted ('this may already account for pending', 'you may want to "
            "confirm with the HR portal'); giving two numbers with no indication of which "
            "is bookable; presenting a single number when a pending amount exists, so the "
            "reader cannot tell if it is gross or net; or offering both readings without "
            "picking one."
        ),
        "not_applicable_criteria": (
            "The answer reports no PTO figures at all -- for example a policy answer, a "
            "refusal, an escalation, or a question about something other than PTO balances."
        ),
    },
]

RUBRIC_BY_ID = {item["id"]: item for item in RUBRIC}

JUDGE_SYSTEM = """You grade the quality of a single answer written by an HR assistant, against exactly one rubric item.

You are a second opinion, independent of any automated check. You are not told what the answer was supposed to say, and you must not speculate about it. Judge only what is in front of you, against the rubric item you are given.

Grade the answer as a reader of it would experience it. The question is context for what was asked, not something to grade.

Be strict about hedging. An answer that gives correct numbers but leaves the reader unsure which number to act on has failed this rubric item, even though nothing in it is false. Do not reward an answer for being cautious or for suggesting the reader check elsewhere; that is the ambiguity this item exists to catch.

Respond by calling the record_verdict tool, always, and nothing else. Do not reply in prose and do not explain yourself outside the tool call. Give one or two sentences of reason, quoting the words that decided it where that helps."""

VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Record the verdict for this rubric item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": list(VERDICTS),
                "description": (
                    "'pass' if the answer meets the pass criteria, 'fail' if it meets the "
                    "fail criteria, 'not_applicable' if the rubric item does not apply."
                ),
            },
            "reason": {
                "type": "string",
                "description": "One or two sentences explaining the verdict.",
            },
        },
        "required": ["verdict", "reason"],
        "additionalProperties": False,
    },
    "strict": True,
}

MAX_TOKENS = 4000

# `tool_choice: {"type": "tool", ...}` would be the obvious way to guarantee a verdict,
# but claude-opus-5-5 rejects forced tool use outright:
#   400 invalid_request_error: tool_choice: type "tool" and "any" are not supported for
#   this model.
# So the verdict is obtained the supported way instead: `auto` with the system prompt
# naming the tool, `strict: True` to keep the arguments schema-valid when it does call,
# and `disable_parallel_tool_use` so at most one verdict comes back. A response that
# arrives without a tool call is reported as verdict "error" rather than guessed at.
TOOL_CHOICE = {"type": "auto", "disable_parallel_tool_use": True}


def build_prompt(question: str, answer: str, item: dict) -> str:
    """The judge's whole view of the world: one rubric item, the question, the answer."""
    return f"""## Rubric item: {item["id"]}

{item["question"]}

**Verdict "pass" when:** {item["pass_criteria"]}

**Verdict "fail" when:** {item["fail_criteria"]}

**Verdict "not_applicable" when:** {item["not_applicable_criteria"]}

## The employee asked

{question}

## The assistant answered

{answer}

Grade the answer above against this one rubric item and record your verdict."""


def _extract_verdict(response: Any) -> dict:
    """Pull the tool input out of a response, or raise ValueError describing what was wrong."""
    content = getattr(response, "content", None)
    if not content:
        raise ValueError("response had no content blocks")

    for block in content:
        if getattr(block, "type", None) == "tool_use" and block.name == VERDICT_TOOL["name"]:
            payload = block.input if isinstance(block.input, dict) else {}
            verdict = payload.get("verdict")
            if verdict not in VERDICTS:
                raise ValueError(f"verdict {verdict!r} is not one of {list(VERDICTS)}")
            return {"verdict": verdict, "reason": str(payload.get("reason", "")).strip()}

    stop = getattr(response, "stop_reason", None)
    text = "".join(b.text for b in content if getattr(b, "type", None) == "text")
    raise ValueError(
        f"no {VERDICT_TOOL['name']} call in the response (stop_reason={stop!r})"
        + (f"; model said: {text[:200]!r}" if text.strip() else "")
    )


def judge(
    question: str,
    answer: str,
    client: Any,
    model: str = JUDGE_MODEL,
    item: dict | None = None,
) -> dict:
    """Grade one answer against one rubric item.

    Returns {item_id, verdict, reason} where verdict is pass / fail / not_applicable, or
    verdict "error" with the reason if the model's response could not be read. Never
    raises: a grader that crashes a run is worse than one that reports it could not grade.
    """
    item = item or RUBRIC[0]
    result = {"item_id": item["id"], "verdict": "error", "reason": ""}

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=JUDGE_SYSTEM,
            tools=[VERDICT_TOOL],
            tool_choice=TOOL_CHOICE,
            messages=[{"role": "user", "content": build_prompt(question, answer, item)}],
        )
    except Exception as e:  # noqa: BLE001 - the judge reports failures, it does not raise them
        result["reason"] = f"judge call failed: {type(e).__name__}: {e}"
        return result

    try:
        result.update(_extract_verdict(response))
    except Exception as e:  # noqa: BLE001 - malformed output is a verdict of "error"
        result["reason"] = f"could not read a verdict: {e}"
    return result


def judge_all(question: str, answer: str, client: Any, model: str = JUDGE_MODEL) -> list[dict]:
    """Grade one answer against every rubric item. One item today."""
    return [judge(question, answer, client, model, item) for item in RUBRIC]


if __name__ == "__main__":  # pragma: no cover - manual spot check
    import sys

    import anthropic

    if len(sys.argv) != 3:
        sys.exit("usage: python -m eval.judge '<question>' '<answer>'")
    print(json.dumps(judge(sys.argv[1], sys.argv[2], anthropic.Anthropic()), indent=2))
