"""Tool definitions (what the model sees) and tool implementations (what actually runs).

Every tool receives a ToolContext built by the application. The model's arguments never
include the requesting user; identity comes from the context alone.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from . import db, permissions
from .config import DB_PATH, HANDBOOK_DIR, OUTBOX_DIR
from .handbook import get_index
from .onboarding import standard_checklist

ESCALATION_CATEGORIES = (
    "compensation",
    "leave/accommodation",
    "conduct/harassment",
    "termination",
    "other",
)

TOOL_DEFINITIONS = [
    {
        "name": "search_handbook",
        "description": (
            "Search the Northwind Robotics employee handbook. Returns the 3 most relevant "
            "sections with their citation (for example 'PTO §2.1'). Use this for any question "
            "about company policy, and cite the returned sections in your answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query."}
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_pto_balance",
        "description": (
            "Look up an employee's PTO balance. Returns available_days (the full accrued "
            "balance, which still includes any pending days), pending_days (time off already "
            "requested but not yet taken), and remaining_after_pending (what is actually left "
            "to book). Pending days are NOT already subtracted from available_days, so report "
            "both figures and say which is which. Only the requesting user's own balance, or "
            "the balance of one of their direct reports, can be returned; any other employee "
            "returns a permission_denied error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string",
                    "description": "Employee id such as E004. Resolve names using the directory.",
                }
            },
            "required": ["employee_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Hand the conversation to the People team. Use this for compensation, leave or "
            "accommodation requests, conduct or harassment concerns, termination questions, "
            "or anything the assistant must not decide itself. Returns a ticket id to give the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": list(ESCALATION_CATEGORIES),
                    "description": "Which team queue the ticket goes to.",
                },
                "summary": {
                    "type": "string",
                    "description": "One or two factual sentences describing what the employee needs.",
                },
            },
            "required": ["category", "summary"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "create_onboarding_checklist",
        "description": (
            "Create the standard onboarding checklist (Onboarding §6) for a new hire, with "
            "due dates derived from their start date. Only the new hire's own direct manager "
            "may do this; anyone else returns a permission_denied error. Call with "
            "confirm=false first to get a preview that creates nothing, show the employee "
            "the tasks and dates, and only call again with confirm=true once they have "
            "explicitly asked you to create it. Returns an error if a checklist already exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string",
                    "description": "Employee id of the new hire, such as E011.",
                },
                "start_date": {
                    "type": "string",
                    "description": "The new hire's start date as an ISO date, YYYY-MM-DD.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "false returns a dry-run preview and writes nothing. true creates the "
                        "tasks. Only pass true after the user has explicitly confirmed."
                    ),
                },
            },
            "required": ["employee_id", "start_date", "confirm"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

TOOL_NAMES = {t["name"] for t in TOOL_DEFINITIONS}


@dataclass
class ToolContext:
    requesting_user_id: str
    db_path: Path = DB_PATH
    outbox_dir: Path = OUTBOX_DIR
    handbook_dir: Path = HANDBOOK_DIR


def search_handbook(ctx: ToolContext, query: str) -> dict:
    results = get_index(ctx.handbook_dir).search(query, k=3)
    return {"ok": True, "query": query, "results": results}


def get_pto_balance(ctx: ToolContext, employee_id: str) -> dict:
    employee_id = (employee_id or "").strip().upper()
    if not permissions.can_view_pto(ctx.requesting_user_id, employee_id, ctx.db_path):
        return {
            "error": "permission_denied",
            "message": (
                f"{ctx.requesting_user_id} may only view their own PTO balance or that of a "
                "direct report. Do not reveal or guess this employee's balance."
            ),
            "employee_id": employee_id,
        }
    row = db.get_pto_balance(employee_id, ctx.db_path)
    if row is None:
        return {"error": "not_found", "message": f"No PTO record for {employee_id}.", "employee_id": employee_id}
    employee = db.get_employee(employee_id, ctx.db_path) or {}
    available, pending = row["available_days"], row["pending_days"]
    remaining = round(available - pending, 2)
    if pending:
        note = (
            f"available_days ({available:g}) is the full accrued balance and still includes the "
            f"{pending:g} pending days already requested but not yet taken, so {remaining:g} days "
            "remain free to book. Give the employee both numbers and say which is which; do not "
            "imply the pending days have already been deducted."
        )
    else:
        note = (
            f"available_days ({available:g}) is the full accrued balance and there are no pending "
            "requests, so all of it is free to book."
        )
    return {
        "ok": True,
        "employee_id": employee_id,
        "name": employee.get("name"),
        "available_days": available,
        "pending_days": pending,
        # Stated outright because the model cannot tell from two bare numbers which
        # convention the HR portal uses, and an eval run showed it hedging.
        "pending_already_deducted": False,
        "remaining_after_pending": remaining,
        "balance_note": note,
    }


def escalate_to_human(ctx: ToolContext, category: str, summary: str) -> dict:
    if category not in ESCALATION_CATEGORIES:
        return {
            "error": "invalid_input",
            "message": f"category must be one of {list(ESCALATION_CATEGORIES)}",
        }
    if not summary or not summary.strip():
        return {"error": "invalid_input", "message": "summary must not be empty"}

    now = datetime.now(timezone.utc)
    ticket_id = f"ESC-{now.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    record = {
        "ticket_id": ticket_id,
        "timestamp": now.isoformat(),
        "requesting_user_id": ctx.requesting_user_id,
        "category": category,
        "summary": summary.strip(),
        "status": "open",
    }
    outbox = Path(ctx.outbox_dir)
    outbox.mkdir(parents=True, exist_ok=True)
    with (outbox / "escalations.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "category": category,
        "message": "A member of the People team will follow up directly with the employee.",
    }


def create_onboarding_checklist(
    ctx: ToolContext, employee_id: str, start_date: str, confirm: bool
) -> dict:
    employee_id = (employee_id or "").strip().upper()
    # Permission first, so an unauthorized caller learns nothing -- not even whether a
    # checklist already exists or whether the date was valid.
    if not permissions.can_create_onboarding_checklist(
        ctx.requesting_user_id, employee_id, ctx.db_path
    ):
        return {
            "error": "permission_denied",
            "message": (
                f"Only {employee_id}'s own direct manager may create their onboarding "
                f"checklist, and {ctx.requesting_user_id} is not. Do not create or describe "
                "a checklist for this employee."
            ),
            "employee_id": employee_id,
        }

    try:
        start = date.fromisoformat((start_date or "").strip())
    except ValueError:
        return {
            "error": "invalid_input",
            "message": f"start_date must be an ISO date like 2026-01-05; got {start_date!r}.",
            "employee_id": employee_id,
        }

    existing = db.onboarding_tasks(employee_id, ctx.db_path)
    if existing:
        return {
            "error": "checklist_exists",
            "message": (
                f"{employee_id} already has an onboarding checklist with {len(existing)} tasks. "
                "Creating another would duplicate it; ask People Ops to amend the existing one."
            ),
            "employee_id": employee_id,
            "existing_task_count": len(existing),
        }

    employee = db.get_employee(employee_id, ctx.db_path) or {}
    tasks = standard_checklist(start)
    if not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "employee_id": employee_id,
            "name": employee.get("name"),
            "start_date": start.isoformat(),
            "task_count": len(tasks),
            "tasks": tasks,
            "message": (
                f"Preview only -- nothing has been created. Show these {len(tasks)} tasks and "
                "their due dates to the user, then call again with confirm=true if they agree."
            ),
        }

    task_ids = db.insert_onboarding_tasks(employee_id, tasks, ctx.db_path)
    return {
        "ok": True,
        "dry_run": False,
        "employee_id": employee_id,
        "name": employee.get("name"),
        "start_date": start.isoformat(),
        "task_count": len(task_ids),
        "created_task_ids": task_ids,
        "tasks": tasks,
        "message": f"Created {len(task_ids)} onboarding tasks for {employee.get('name') or employee_id}.",
    }


_IMPLEMENTATIONS = {
    "search_handbook": search_handbook,
    "get_pto_balance": get_pto_balance,
    "escalate_to_human": escalate_to_human,
    "create_onboarding_checklist": create_onboarding_checklist,
}


def execute_tool(name: str, args: dict, ctx: ToolContext) -> tuple[dict, str]:
    """Run a tool and return (result, status). Never raises: errors come back as JSON."""
    impl = _IMPLEMENTATIONS.get(name)
    if impl is None:
        return {"error": "unknown_tool", "message": f"No tool named {name!r}."}, "unknown_tool"
    try:
        result = impl(ctx, **dict(args or {}))
    except TypeError as e:
        result = {"error": "invalid_input", "message": str(e)}
    except Exception as e:  # noqa: BLE001 - the loop must survive any tool failure
        result = {"error": "tool_error", "message": f"{type(e).__name__}: {e}"}
    status = result.get("error", "ok") if isinstance(result, dict) else "ok"
    return result, status
