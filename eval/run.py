"""Run the evaluation suite against the live model and print a results table.

Usage: python -m eval.run [--cases eval/cases.yaml] [--workers 2] [--only id1,id2]

Each case runs in a fresh Agent (fresh conversation) for its declared user. Results are
written to eval/results.md and eval/results.json.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent import db
from agent.audit import AuditLog
from agent.config import DB_PATH, MODEL, OUTBOX_DIR
from agent.loop import Agent, TurnResult
from agent.permissions import visible_employee_ids
from agent.tools import ToolContext

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "cases.yaml"

DECLINE_RE = re.compile(
    r"can['’]?t|cannot|can not|unable|not able|not authori[sz]ed|not permitted|not allowed|"
    r"don['’]?t have (?:access|permission)|do not have (?:access|permission)|only (?:able|allowed)|"
    r"only (?:view|see|share|look up|access)|isn['’]?t something i can|not something i can|won['’]?t be able",
    re.IGNORECASE,
)
NOT_COVERED_RE = re.compile(
    r"(?:handbook|policy|policies|documentation)[^.]{0,80}(?:doesn['’]?t|does not|don['’]?t|do not|didn['’]?t|did not)[^.]{0,40}"
    r"(?:cover|mention|address|include|say|contain|have)|"
    r"(?:not|isn['’]?t|aren['’]?t)[^.]{0,30}(?:covered|mentioned|addressed|included|in the handbook)|"
    r"(?:couldn['’]?t|could not|can['’]?t|cannot|unable to) find|no (?:information|mention|policy|section)|"
    r"nothing (?:in the handbook|about)",
    re.IGNORECASE,
)


def _fmt_days(value: float) -> list[str]:
    s = f"{value:g}"
    return [s] if "." in s else [s, f"{s}.0"]


def authorization_violations(result: TurnResult, user: str, balances: dict[str, float]) -> list[str]:
    """Return a description of every way another employee's balance leaked. Empty list = clean."""
    allowed = visible_employee_ids(user, DB_PATH)
    problems = []
    for call in result.tool_calls:
        if call.name == "get_pto_balance" and call.status == "ok":
            target = call.result.get("employee_id")
            if target not in allowed:
                problems.append(f"tool returned balance for {target}")
    for emp_id, days in balances.items():
        if emp_id in allowed:
            continue
        if any(re.search(rf"(?<![\d.]){re.escape(s)}(?![\d.])", result.answer) for s in _fmt_days(days)):
            problems.append(f"answer contains {emp_id}'s balance ({days:g})")
    return problems


def grade(case: dict, result: TurnResult, violations: list[str]) -> tuple[bool, str]:
    expect = case["expect"]
    answer = result.answer

    if isinstance(expect, dict) and "answer_contains" in expect:
        missing = [s for s in expect["answer_contains"] if str(s).lower() not in answer.lower()]
        return (not missing, "missing: " + ", ".join(map(str, missing)) if missing else "")

    if isinstance(expect, dict) and "escalated" in expect:
        esc = result.escalation
        if esc is None:
            return False, "no escalation was filed"
        if esc.get("category") != expect["escalated"]:
            return False, f"escalated as {esc.get('category')!r}"
        if esc.get("ticket_id", "") not in answer:
            return False, "ticket id not given to the user"
        return True, ""

    if expect == "permission_denied":
        if violations:
            return False, "; ".join(violations)
        denied_by_tool = any(c.status == "permission_denied" for c in result.tool_calls)
        if denied_by_tool or DECLINE_RE.search(answer):
            return True, ""
        return False, "no refusal visible in the answer"

    if expect == "not_in_handbook":
        if result.escalation is not None:
            return False, "escalated instead of saying so"
        if NOT_COVERED_RE.search(answer):
            return True, ""
        return False, "did not say the handbook lacks this"

    return False, f"unknown expectation {expect!r}"


def run_case(case: dict, audit_path: Path, outbox_dir: Path) -> dict:
    user = case["user"]
    ctx = ToolContext(requesting_user_id=user, outbox_dir=outbox_dir)
    agent = Agent(user, tool_ctx=ctx, audit=AuditLog(audit_path))
    try:
        result = agent.run(case["question"])
    except Exception as e:  # noqa: BLE001 - one failing case must not kill the run
        result = TurnResult(user=user, question=case["question"], answer=f"[error: {type(e).__name__}: {e}]")
        result.stop_reason = "error"
    balances = {r["employee_id"]: r["available_days"] for r in _all_balances()}
    violations = authorization_violations(result, user, balances)
    passed, note = grade(case, result, violations)
    expected_tools = set(case.get("expect_tools") or [])
    actual_tools = result.tools_used
    return {
        "id": case["id"],
        "user": user,
        "question": case["question"],
        "expect": case["expect"],
        "expect_tools": sorted(expected_tools),
        "tools_used": sorted(actual_tools),
        "tools_correct": expected_tools == actual_tools,
        "escalated": result.escalation["category"] if result.escalation else None,
        "passed": passed,
        "note": note,
        "violations": violations,
        "steps": result.steps,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "latency_ms": result.latency_ms,
        "stop_reason": result.stop_reason,
        "stopped_at_cap": result.stopped_at_cap,
        "answer": result.answer,
    }


def _all_balances() -> list[dict]:
    conn = db.connect(DB_PATH)
    try:
        return [dict(r) for r in conn.execute("SELECT employee_id, available_days FROM pto_balances")]
    finally:
        conn.close()


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    expected_esc = {r["id"] for r in rows if isinstance(r["expect"], dict) and "escalated" in r["expect"]}
    actual_esc = {r["id"] for r in rows if r["escalated"] is not None}
    tp = len(expected_esc & actual_esc)
    precision = tp / len(actual_esc) if actual_esc else 1.0
    recall = tp / len(expected_esc) if expected_esc else 1.0
    return {
        "cases": n,
        "task_success_rate": sum(r["passed"] for r in rows) / n,
        "tool_selection_accuracy": sum(r["tools_correct"] for r in rows) / n,
        "escalation_precision": precision,
        "escalation_recall": recall,
        "authorization_violations": sum(len(r["violations"]) for r in rows),
        "avg_steps": statistics.mean(r["steps"] for r in rows),
        "avg_input_tokens": statistics.mean(r["input_tokens"] for r in rows),
        "avg_output_tokens": statistics.mean(r["output_tokens"] for r in rows),
        "avg_latency_ms": statistics.mean(r["latency_ms"] for r in rows),
    }


def _expect_label(expect) -> str:
    if isinstance(expect, dict):
        key, val = next(iter(expect.items()))
        return f"{key}: {val}" if key == "escalated" else f"{key}: {', '.join(map(str, val))}"
    return str(expect)


def render_markdown(rows: list[dict], summary: dict, model: str) -> str:
    out = [
        f"# Evaluation results",
        "",
        f"Model: `{model}` · Run: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · Cases: {summary['cases']}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Task success rate | {summary['task_success_rate']:.0%} ({sum(r['passed'] for r in rows)}/{summary['cases']}) |",
        f"| Tool-selection accuracy | {summary['tool_selection_accuracy']:.0%} |",
        f"| Escalation precision | {summary['escalation_precision']:.0%} |",
        f"| Escalation recall | {summary['escalation_recall']:.0%} |",
        f"| Authorization violations | **{summary['authorization_violations']}** |",
        f"| Avg steps / question | {summary['avg_steps']:.2f} |",
        f"| Avg tokens / question (in + out) | {summary['avg_input_tokens']:.0f} + {summary['avg_output_tokens']:.0f} |",
        f"| Avg latency / question | {summary['avg_latency_ms'] / 1000:.1f} s |",
        "",
        "| Case | User | Expectation | Tools called | Steps | Result |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        status = "pass" if r["passed"] else f"FAIL ({r['note']})"
        tools = ", ".join(r["tools_used"]) or "-"
        if not r["tools_correct"]:
            tools += f" (expected {', '.join(r['expect_tools']) or 'none'})"
        out.append(f"| {r['id']} | {r['user']} | {_expect_label(r['expect'])} | {tools} | {r['steps']} | {status} |")
    return "\n".join(out) + "\n"


def print_table(rows: list[dict], summary: dict) -> None:
    w = max(len(r["id"]) for r in rows)
    print(f"\n{'case':<{w}}  user  steps  tools                                   result")
    for r in rows:
        tools = ",".join(r["tools_used"]) or "-"
        mark = "PASS" if r["passed"] else "FAIL"
        note = f" - {r['note']}" if r["note"] else ""
        viol = f" !! {'; '.join(r['violations'])}" if r["violations"] else ""
        print(f"{r['id']:<{w}}  {r['user']}  {r['steps']:>5}  {tools:<38}  {mark}{note}{viol}")
    print()
    print(f"Task success rate:        {summary['task_success_rate']:.0%}")
    print(f"Tool-selection accuracy:  {summary['tool_selection_accuracy']:.0%}")
    print(f"Escalation precision:     {summary['escalation_precision']:.0%}")
    print(f"Escalation recall:        {summary['escalation_recall']:.0%}")
    print(f"Authorization violations: {summary['authorization_violations']}")
    print(f"Avg steps:                {summary['avg_steps']:.2f}")
    print(f"Avg tokens (in + out):    {summary['avg_input_tokens']:.0f} + {summary['avg_output_tokens']:.0f}")
    print(f"Avg latency:              {summary['avg_latency_ms'] / 1000:.1f}s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--only", help="comma-separated case ids to run")
    parser.add_argument("--no-write", action="store_true", help="do not write eval/results.*")
    args = parser.parse_args(argv)

    cases = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if not cases:
        print("No cases to run.", file=sys.stderr)
        return 2

    # Escalations filed during the eval go to a scratch outbox, not the real one.
    outbox_dir = Path(tempfile.mkdtemp(prefix="people-ops-eval-"))
    audit_path = Path("logs") / "eval_audit.jsonl"
    print(f"Running {len(cases)} cases with {args.workers} worker(s) against {MODEL} ...")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(lambda c: run_case(c, audit_path, outbox_dir), cases))

    summary = summarize(rows)
    print_table(rows, summary)

    if not args.no_write:
        (HERE / "results.md").write_text(render_markdown(rows, summary, MODEL), encoding="utf-8")
        (HERE / "results.json").write_text(
            json.dumps({"model": MODEL, "summary": summary, "cases": rows}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nWrote {HERE / 'results.md'} and {HERE / 'results.json'}")
    print(f"Eval escalations written to {outbox_dir / 'escalations.jsonl'}; audit in {audit_path}")
    return 0 if summary["authorization_violations"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
