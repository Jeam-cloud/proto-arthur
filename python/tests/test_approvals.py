"""Approval broker: default-deny semantics."""

import asyncio

from security.approvals import ApprovalBroker


async def test_approve_resolves_true():
    broker = ApprovalBroker(timeout_s=1.0)
    approval = broker.create("email_send", "Send email", {})

    async def user_clicks():
        await asyncio.sleep(0.01)
        assert broker.resolve(approval.id, True)

    resolution, _ = await asyncio.gather(broker.wait(approval.id), user_clicks())
    assert resolution.approved is True
    assert resolution.edited_args is None


async def test_timeout_denies():
    broker = ApprovalBroker(timeout_s=0.03)
    approval = broker.create("email_send", "Send email", {})
    resolution = await broker.wait(approval.id)
    assert resolution.approved is False  # user walked away -> deny


async def test_deny_resolves_false():
    broker = ApprovalBroker(timeout_s=1.0)
    approval = broker.create("write_file", "Overwrite main.py", {})

    async def user_declines():
        await asyncio.sleep(0.01)
        broker.resolve(approval.id, False)

    resolution, _ = await asyncio.gather(broker.wait(approval.id), user_declines())
    assert resolution.approved is False


async def test_edited_args_carry_through_the_resolution():
    """The dialog can send back a reworded draft alongside approval -- that
    payload has to survive the Future round trip so the loop can re-validate
    and run with what the user actually approved, not the model's original."""
    broker = ApprovalBroker(timeout_s=1.0)
    approval = broker.create("email_send", "Send email", {}, args={"to": ["a@x.com"], "body": "draft"})

    async def user_edits_and_sends():
        await asyncio.sleep(0.01)
        broker.resolve(approval.id, True, {"to": ["a@x.com"], "subject": "s", "body": "final"})

    resolution, _ = await asyncio.gather(broker.wait(approval.id), user_edits_and_sends())
    assert resolution.approved is True
    assert resolution.edited_args == {"to": ["a@x.com"], "subject": "s", "body": "final"}
    assert approval.args == {"to": ["a@x.com"], "body": "draft"}  # original, untouched


async def test_double_resolve_is_idempotent():
    broker = ApprovalBroker(timeout_s=1.0)
    approval = broker.create("t", "s", {})
    assert broker.resolve(approval.id, True) is True
    assert broker.resolve(approval.id, False) is False  # second click ignored


async def test_unknown_id_rejected():
    broker = ApprovalBroker()
    assert broker.resolve("nonsense", True) is False


async def test_pending_list_clears_after_wait():
    broker = ApprovalBroker(timeout_s=0.03)
    approval = broker.create("t", "s", {})
    assert len(broker.pending()) == 1
    await broker.wait(approval.id)
    assert broker.pending() == []
