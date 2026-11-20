from __future__ import annotations

from pathlib import Path

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from agent.audit import AuditLog
from agent.tools import ToolContext


@pytest.fixture
def outbox(tmp_path: Path) -> Path:
    return tmp_path / "outbox"


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def ctx_factory(outbox: Path):
    def make(user: str) -> ToolContext:
        return ToolContext(requesting_user_id=user, outbox_dir=outbox)

    return make


def tool_use_message(n: int, name: str, args: dict) -> Message:
    return Message(
        id=f"msg_{n}",
        type="message",
        role="assistant",
        model="fake-model",
        content=[ToolUseBlock(type="tool_use", id=f"toolu_{n}", name=name, input=args)],
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(input_tokens=100, output_tokens=20),
    )


def text_message(n: int, text: str) -> Message:
    return Message(
        id=f"msg_{n}",
        type="message",
        role="assistant",
        model="fake-model",
        content=[TextBlock(type="text", text=text)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=100, output_tokens=20),
    )


class FakeClient:
    """Stands in for anthropic.Anthropic. `script` is a list of callables (step -> Message);
    the last one repeats forever."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.requests: list[dict] = []
        self.messages = self  # so client.messages.create(...) works

    def create(self, **kwargs) -> Message:
        self.calls += 1
        self.requests.append(kwargs)
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        return step(self.calls)
