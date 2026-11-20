"""The agent loop: Claude Messages API with tool use, a hard step cap, and an audit record per turn.

One `run()` call is one user turn. The loop:
  1. sends the conversation plus tool definitions to the model,
  2. if the model asks for tools, runs them (with the app-set identity) and appends the results,
  3. repeats until the model answers in text or MAX_STEPS API calls have been made.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import anthropic

from .audit import AuditLog
from .config import EFFORT, MAX_STEPS, MAX_TOKENS, MODEL
from .prompts import build_system_prompt
from .tools import TOOL_DEFINITIONS, ToolContext, execute_tool

STEP_CAP_MESSAGE = (
    "I wasn't able to complete this request within my step limit, so I've passed it to the "
    "People team instead. Your ticket id is {ticket_id}; someone will follow up with you directly."
)
REFUSAL_MESSAGE = (
    "I can't help with that request. If it's an HR matter, I can escalate it to the People team for you."
)


@dataclass
class ToolCallRecord:
    name: str
    args: dict
    status: str
    result: dict


@dataclass
class TurnResult:
    user: str
    question: str
    answer: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    stop_reason: str | None = None
    stopped_at_cap: bool = False

    @property
    def tools_used(self) -> set[str]:
        return {c.name for c in self.tool_calls}

    @property
    def escalation(self) -> dict | None:
        for call in self.tool_calls:
            if call.name == "escalate_to_human" and call.status == "ok":
                return call.result
        return None

    def to_audit_record(self) -> dict:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": self.user,
            "question": self.question,
            "tool_calls": [
                {"name": c.name, "args": c.args, "status": c.status} for c in self.tool_calls
            ],
            "final_answer": self.answer,
            "steps": self.steps,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "stop_reason": self.stop_reason,
            "stopped_at_cap": self.stopped_at_cap,
        }


class Agent:
    """A conversation for one verified user. Identity is fixed for the life of the object."""

    def __init__(
        self,
        requesting_user_id: str,
        *,
        client: Any | None = None,
        tool_ctx: ToolContext | None = None,
        audit: AuditLog | None = None,
        model: str = MODEL,
        max_steps: int = MAX_STEPS,
        effort: str = EFFORT,
    ):
        self.user = requesting_user_id
        self.tool_ctx = tool_ctx or ToolContext(requesting_user_id=requesting_user_id)
        if self.tool_ctx.requesting_user_id != requesting_user_id:
            raise ValueError("tool_ctx.requesting_user_id must match the agent's user")
        self.client = client or anthropic.Anthropic()
        self.audit = audit or AuditLog()
        self.model = model
        self.max_steps = max_steps
        self.effort = effort
        self.system_prompt = build_system_prompt(requesting_user_id, self.tool_ctx.db_path)
        self.messages: list[dict] = []

    def _create(self) -> Any:
        return self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=self.system_prompt,
            tools=TOOL_DEFINITIONS,
            messages=self.messages,
            output_config={"effort": self.effort},
        )

    def run(self, question: str) -> TurnResult:
        started = time.perf_counter()
        result = TurnResult(user=self.user, question=question, answer="")
        self.messages.append({"role": "user", "content": question})

        while result.steps < self.max_steps:
            result.steps += 1
            response = self._create()
            result.input_tokens += response.usage.input_tokens
            result.output_tokens += response.usage.output_tokens
            result.stop_reason = response.stop_reason
            # Append the whole content list (text, tool_use and thinking blocks) unchanged.
            self.messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "tool_use" and tool_uses:
                tool_results = []
                for tu in tool_uses:
                    args = dict(tu.input) if isinstance(tu.input, dict) else {}
                    tool_result, status = execute_tool(tu.name, args, self.tool_ctx)
                    result.tool_calls.append(ToolCallRecord(tu.name, args, status, tool_result))
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": json.dumps(tool_result),
                            "is_error": status in ("tool_error", "unknown_tool"),
                        }
                    )
                # All results for one assistant turn go back in a single user message.
                self.messages.append({"role": "user", "content": tool_results})
                continue

            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "refusal":
                text = REFUSAL_MESSAGE
            elif response.stop_reason == "max_tokens":
                text = (text + "\n\n[Response was cut off; please ask again more narrowly.]").strip()
            result.answer = text or "I don't have an answer for that. Would you like me to escalate it?"
            break
        else:
            result.stopped_at_cap = True
            result.answer = self._handle_step_cap(question, result)

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        self.audit.write(result.to_audit_record())
        return result

    def _handle_step_cap(self, question: str, result: TurnResult) -> str:
        """Escalate in code (not via the model) and close the turn with a graceful message."""
        summary = (
            f"Assistant hit its {self.max_steps}-step limit without answering. "
            f"Question from {self.user}: {question[:300]}"
        )
        args = {"category": "other", "summary": summary}
        tool_result, status = execute_tool("escalate_to_human", args, self.tool_ctx)
        result.tool_calls.append(ToolCallRecord("escalate_to_human", args, status, tool_result))
        answer = STEP_CAP_MESSAGE.format(ticket_id=tool_result.get("ticket_id", "unavailable"))
        # Keep the transcript well-formed for any follow-up turn.
        self.messages.append({"role": "assistant", "content": answer})
        return answer
