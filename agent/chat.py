"""Interactive CLI: python -m agent.chat --user E004"""
from __future__ import annotations

import argparse
import os
import sys

from . import db
from .loop import Agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chat with the Northwind Robotics People Ops assistant.")
    parser.add_argument("--user", required=True, help="Employee id you are signed in as, e.g. E004")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Add it to .env and try again.", file=sys.stderr)
        return 2
    employee = db.get_employee(args.user.upper())
    if employee is None:
        print(f"Unknown employee id {args.user!r}.", file=sys.stderr)
        return 2

    agent = Agent(employee["id"])
    print(f"Signed in as {employee['name']} ({employee['id']}, {employee['title']}). Type 'quit' to exit.\n")
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"quit", "exit", "q"}:
            break
        result = agent.run(question)
        print(f"\nassistant> {result.answer}\n")
        tools = ", ".join(f"{c.name}[{c.status}]" for c in result.tool_calls) or "none"
        print(
            f"  [steps={result.steps} tools={tools} tokens={result.input_tokens}+{result.output_tokens} "
            f"latency={result.latency_ms}ms]\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
