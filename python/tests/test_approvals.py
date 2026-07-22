"""Approval broker: default-deny semantics."""

import asyncio

from security.approvals import ApprovalBroker


async def test_approve_resolves_true():
    broker = ApprovalBroker(timeout_s=1.0)
    approval = broker.create("email_send", "Send email", {})

    async def user_clicks():
        await asyncio.sleep(0.01)
        assert broker.resolve(approval.id, True)

    approved, _ = await asyncio.gather(broker.wait(approval.id), user_clicks())
    assert approved is True


async def test_timeout_denies():
    broker = ApprovalBroker(timeout_s=0.03)
    approval = broker.create("email_send", "Send email", {})
    assert await broker.wait(approval.id) is False  # user walked away -> deny


async def test_deny_resolves_false():
    broker = ApprovalBroker(timeout_s=1.0)
    approval = broker.create("write_file", "Overwrite main.py", {})

    async def user_declines():
        await asyncio.sleep(0.01)
        broker.resolve(approval.id, False)

    approved, _ = await asyncio.gather(broker.wait(approval.id), user_declines())
    assert approved is False


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
