"""Human-confirmation broker for irreversible actions.

Flow: the agent loop hits a CONFIRM-risk tool call -> broker.create() makes a
pending approval + asyncio.Future -> an `approval_required` SSE event reaches
the UI -> the user clicks Approve/Deny (optionally after EDITING the draft,
e.g. rewording an email before it sends) -> POST /approvals/{id} resolves the
Future -> the awaiting loop continues with whatever the user actually approved.

WHY default-deny on timeout: if the user walks away, the safe outcome is "the
email was not sent", never the reverse. Futures also resolve to deny when the
stream is cancelled (user hit Stop).

WHY edits flow through here rather than being applied client-side and trusted:
the Future carries the user's edited args back to the SAME Pydantic validation
gate the model's original args went through (see agent/loop.py _execute_one).
A person typing a bad email address should get the same "invalid arguments"
handling a model would, not a special trusted bypass.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from time import time


@dataclass
class ApprovalResolution:
    approved: bool
    edited_args: dict | None = None  # None = run with the original args unchanged


@dataclass
class PendingApproval:
    id: str
    tool: str
    summary: str
    args_preview: dict
    args: dict = field(default_factory=dict)  # structured (unstringified) args, for edit forms
    created_at: float = field(default_factory=time)


class ApprovalBroker:
    def __init__(self, timeout_s: float = 120.0):
        self._timeout = timeout_s
        self._pending: dict[str, PendingApproval] = {}
        self._futures: dict[str, asyncio.Future[ApprovalResolution]] = {}

    def create(self, tool: str, summary: str, args_preview: dict, args: dict | None = None) -> PendingApproval:
        approval = PendingApproval(
            id=uuid.uuid4().hex, tool=tool, summary=summary,
            args_preview=args_preview, args=args or {},
        )
        self._pending[approval.id] = approval
        self._futures[approval.id] = asyncio.get_running_loop().create_future()
        return approval

    async def wait(self, approval_id: str) -> ApprovalResolution:
        fut = self._futures.get(approval_id)
        if fut is None:
            return ApprovalResolution(approved=False)
        try:
            return await asyncio.wait_for(fut, timeout=self._timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return ApprovalResolution(approved=False)
        finally:
            self._pending.pop(approval_id, None)
            self._futures.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool, edited_args: dict | None = None) -> bool:
        """Returns False for unknown/already-resolved ids (idempotent — double
        clicks and races with timeouts are normal, not errors)."""
        fut = self._futures.get(approval_id)
        if fut is None or fut.done():
            return False
        fut.set_result(ApprovalResolution(approved=approved, edited_args=edited_args))
        return True

    def pending(self) -> list[PendingApproval]:
        return list(self._pending.values())
