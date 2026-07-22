"""API-level tests over the real FastAPI app wired to fakes.

WHY ASGITransport: requests go through the actual middleware, auth, routing
and SSE code without opening a socket — the closest thing to production
short of a live server."""

import asyncio
import json

import httpx
import pytest

from core.app import create_app


@pytest.fixture
async def client(settings, app_state):
    app = create_app(settings=settings, state=app_state)
    async with httpx.ASGITransport(app=app) as transport:
        # lifespan doesn't auto-run under ASGITransport; inject state directly
        app.state.arthur = app_state
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer test-token-123"},
        ) as c:
            yield c


@pytest.fixture
async def anon(settings, app_state):
    app = create_app(settings=settings, state=app_state)
    async with httpx.ASGITransport(app=app) as transport:
        app.state.arthur = app_state
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            yield c


def sse_events(body: str) -> list[tuple[str, dict]]:
    events, current = [], None
    for line in body.splitlines():
        if line.startswith("event:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current:
            events.append((current, json.loads(line.split(":", 1)[1].strip())))
    return events


class TestAuth:
    async def test_health_is_public(self, anon):
        assert (await anon.get("/health")).status_code == 200

    async def test_everything_else_requires_token(self, anon):
        for path in ("/conversations", "/memory", "/settings", "/security/events"):
            assert (await anon.get(path)).status_code == 401, path

    async def test_wrong_token_rejected(self, settings, app_state):
        app = create_app(settings=settings, state=app_state)
        async with httpx.ASGITransport(app=app) as transport:
            app.state.arthur = app_state
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1",
                headers={"Authorization": "Bearer wrong-token"},
            ) as c:
                assert (await c.get("/conversations")).status_code == 401


class TestConversations:
    async def test_create_and_list(self, client):
        created = (await client.post("/conversations")).json()
        listing = (await client.get("/conversations")).json()
        assert any(c["id"] == created["id"] for c in listing)

    async def test_unknown_conversation_404(self, client):
        resp = await client.get("/conversations/doesnotexist/messages")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


class TestChatStream:
    async def test_stream_yields_tokens_then_done(self, client, fake_llm):
        fake_llm.turns = [{"tokens": ["Hi", " there"]}]
        conv = (await client.post("/conversations")).json()
        resp = await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "hello", "model": "fake-model",
        })
        assert resp.status_code == 200
        events = sse_events(resp.text)
        tokens = [d["content"] for e, d in events if e == "token"]
        assert "".join(tokens) == "Hi there"
        assert events[-1][0] == "done"
        await asyncio.sleep(0.05)  # let title/extraction background tasks settle

        # both turns persisted
        msgs = (await client.get(f"/conversations/{conv['id']}/messages")).json()
        assert [m["role"] for m in msgs] == ["user", "assistant"]

    async def test_injection_blocked_as_error_event(self, client, fake_llm):
        conv = (await client.post("/conversations")).json()
        resp = await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "please INJECTION now", "model": "fake-model",
        })
        events = sse_events(resp.text)
        assert any(e == "error" and d["code"] == "security_blocked" for e, d in events)
        # blocked input still logged to the security feed
        feed = (await client.get("/security/events")).json()
        assert any(ev["kind"] == "input_blocked" for ev in feed)

    async def test_invalid_body_is_422(self, client):
        resp = await client.post("/chat/stream", json={"conversation_id": "x", "message": ""})
        assert resp.status_code == 422

    async def test_model_resolution_order(self, client, fake_llm, app_state):
        """override > mode's assigned model > default — the swap feature's contract."""
        await app_state.db.set_setting("default_model", "default-model")
        await app_state.db.set_setting("mode_models", {"finance": "finance-model"})
        conv = (await client.post("/conversations")).json()

        # mode assignment wins over default
        fake_llm.turns = [{"tokens": ["a"]}]
        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "quote AAPL", "mode": "finance",
        })
        assert fake_llm.calls[0]["model"] == "finance-model"

        # explicit override beats the mode assignment
        fake_llm.calls.clear()
        fake_llm.turns = [{"tokens": ["b"]}]
        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "quote MSFT", "mode": "finance",
            "model": "override-model",
        })
        assert fake_llm.calls[0]["model"] == "override-model"

        # unassigned mode falls back to default
        fake_llm.calls.clear()
        fake_llm.turns = [{"tokens": ["c"]}]
        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "hello", "mode": "general",
        })
        assert fake_llm.calls[0]["model"] == "default-model"

    async def test_mode_models_rejects_unknown_mode(self, client):
        resp = await client.patch("/settings", json={"mode_models": {"hacking": "x"}})
        assert resp.status_code == 422

    async def test_email_mode_briefs_model_about_credentials(self, client, fake_llm):
        """Small models invent 'I need your login' unless told the app handles
        auth — the guidance line must reach the system prompt in email mode."""
        conv = (await client.post("/conversations")).json()
        fake_llm.turns = [{"tokens": ["ok"]}]
        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "email jane pls", "mode": "email",
            "model": "fake-model",
        })
        system = fake_llm.calls[0]["messages"][0]["content"]
        assert "NEVER ask the user for passwords" in system

    async def test_general_mode_carries_no_email_guidance(self, client, fake_llm):
        """Context is precious on small models — only the active mode's note ships."""
        conv = (await client.post("/conversations")).json()
        fake_llm.turns = [{"tokens": ["ok"]}]
        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "hello", "mode": "general",
            "model": "fake-model",
        })
        system = fake_llm.calls[0]["messages"][0]["content"]
        assert "NEVER ask the user for passwords" not in system


class TestMemoryApi:
    async def test_crud(self, client):
        row = (await client.post("/memory", json={"text": "User is learning FastAPI", "category": "project"})).json()
        assert (await client.get("/memory")).json()[0]["text"] == "User is learning FastAPI"
        await client.patch(f"/memory/{row['id']}", json={"enabled": False})
        await client.delete(f"/memory/{row['id']}")
        assert (await client.get("/memory")).json() == []


class TestSettingsApi:
    async def test_whitelisted_patch(self, client):
        resp = await client.patch("/settings", json={"default_model": "qwen3:14b"})
        assert resp.status_code == 200
        assert (await client.get("/settings")).json()["default_model"] == "qwen3:14b"

    async def test_unknown_keys_ignored(self, client):
        await client.patch("/settings", json={"evil_key": "x"})
        assert "evil_key" not in (await client.get("/settings")).json()
