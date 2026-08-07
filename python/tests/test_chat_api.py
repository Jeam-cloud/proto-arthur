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


class TestAttachmentOnlyMessages:
    """A message can be carried entirely by its attachments.

    `ChatRequest.message` had `min_length=1`, so dropping a screenshot and
    pressing send with no words was rejected by Pydantic -- which the UI could
    only report as "Stream failed (422)". The emptiness check still exists, it
    just moved to where it can see the attachments.
    """

    async def test_empty_text_with_no_attachments_is_refused_readably(self, client, fake_llm):
        conv = (await client.post("/conversations")).json()
        resp = await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "   ", "mode": "general",
            "model": "fake-model",
        })
        # A named error, not a bare 422.
        assert resp.status_code != 422
        assert "attach a file" in resp.json()["error"]["message"]

    async def test_the_nearer_problem_is_reported_first(self, client, fake_llm):
        # No model configured AND an empty composer. "No model selected" is true
        # but it is not the problem the user has in front of them.
        conv = (await client.post("/conversations")).json()
        resp = await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "", "mode": "general",
        })
        assert "attach a file" in resp.json()["error"]["message"]

    async def test_an_attached_image_actually_reaches_the_model(self, client, fake_llm, app_state):
        """END TO END through the route, not just _build_messages.

        Unit-testing the prompt builder proved the images key was set; it did
        NOT prove the key survives the route, stream_reply, and the agent loop
        to arrive at the client. That gap is where the bug kept hiding.
        """
        import base64

        fake_llm.turns = [{"tokens": ["ok"]}]
        fake_llm.caps = {"completion", "vision"}
        conv = (await client.post("/conversations")).json()
        png = b"\x89PNG\r\n\x1a\nREALBYTES"
        await app_state.attachments.add_bytes(conv["id"], "flower.png", png)

        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "do you see this image",
            "mode": "general", "model": "fake-model",
        })

        sent = fake_llm.calls[0]["messages"]
        with_images = [m for m in sent if m.get("images")]
        assert with_images, "no message carried images to the model"
        assert base64.b64decode(with_images[0]["images"][0]) == png

    async def test_the_user_turn_is_not_sent_twice(self, client, fake_llm):
        """`history_for_model` reads the messages table, and the user turn is
        persisted BEFORE the prompt is assembled -- so history already ended
        with the message that then got appended again. The model saw the same
        question twice, back to back, and only the second copy carried images.

        Two consecutive user messages is also the shape most likely to be
        collapsed or skipped by a chat template, which is how an attached image
        can vanish between Arthur and the model.
        """
        fake_llm.turns = [{"tokens": ["ok"]}]
        conv = (await client.post("/conversations")).json()
        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "unique question here",
            "mode": "general", "model": "fake-model",
        })

        sent = fake_llm.calls[0]["messages"]
        copies = [m for m in sent if (m.get("content") or "").startswith("unique question here")]
        assert len(copies) == 1, f"user turn appeared {len(copies)} times"

    async def test_an_attachment_only_turn_is_never_empty(self, client, fake_llm, app_state):
        # An empty-content message carrying images is the exact shape a template
        # drops. It must always have something renderable in it.
        fake_llm.turns = [{"tokens": ["ok"]}]
        fake_llm.caps = {"completion", "vision"}
        conv = (await client.post("/conversations")).json()
        await app_state.attachments.add_bytes(conv["id"], "shot.png", b"\x89PNG")

        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "", "mode": "general",
            "model": "fake-model",
        })

        sent = fake_llm.calls[0]["messages"]
        [with_images] = [m for m in sent if m.get("images")]
        assert with_images["content"].strip(), "a message with images must not be empty"

    async def test_tools_are_dropped_on_a_turn_carrying_an_image(self, client, fake_llm, app_state):
        """Vision and tool-calling live in different branches of a chat
        template, and small models routinely mishandle the combination -- the
        tool block renders and the image part is dropped, so the model reports
        it received no image. Looking at a picture needs no tools, so the turn
        gives them up rather than risk the image."""
        fake_llm.turns = [{"tokens": ["a flower"]}]
        fake_llm.caps = {"completion", "vision"}
        conv = (await client.post("/conversations")).json()
        await app_state.attachments.add_bytes(conv["id"], "flower.png", b"\x89PNG")

        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "what is this",
            "mode": "general", "model": "fake-model",
        })
        assert fake_llm.calls[0]["tools"] is None

    async def test_tools_survive_a_turn_with_no_image(self, client, fake_llm):
        # The drop is scoped to the image turn only; ordinary messages keep the
        # full tool set.
        fake_llm.turns = [{"tokens": ["hi"]}]
        conv = (await client.post("/conversations")).json()
        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "hello",
            "mode": "general", "model": "fake-model",
        })
        assert fake_llm.calls[0]["tools"], "a normal turn must still get tools"

    async def test_a_blind_model_gets_told_instead_of_the_bytes(self, client, fake_llm, app_state):
        fake_llm.turns = [{"tokens": ["ok"]}]
        fake_llm.caps = {"completion"}  # no vision
        conv = (await client.post("/conversations")).json()
        await app_state.attachments.add_bytes(conv["id"], "flower.png", b"\x89PNG")

        await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "see this?",
            "mode": "general", "model": "fake-model",
        })

        sent = fake_llm.calls[0]["messages"]
        assert not any(m.get("images") for m in sent)
        assert any("cannot see images" in (m.get("content") or "") for m in sent)

    async def test_empty_text_with_an_attachment_is_accepted(self, client, fake_llm, app_state):
        fake_llm.turns = [{"tokens": ["I", " see", " it"]}]
        conv = (await client.post("/conversations")).json()
        await app_state.attachments.add_bytes(conv["id"], "shot.png", b"\x89PNG\r\n\x1a\n")

        resp = await client.post("/chat/stream", json={
            "conversation_id": conv["id"], "message": "", "mode": "general",
            "model": "fake-model",
        })
        assert resp.status_code == 200
        assert "I" in resp.text

        # ...and the file is bound to the message that was just sent, so the
        # transcript shows what was asked about.
        msgs = (await client.get(f"/conversations/{conv['id']}/messages")).json()
        user = [m for m in msgs if m["role"] == "user"][0]
        assert [a["filename"] for a in user["attachments"]] == ["shot.png"]


class TestConversationWorkspace:
    """A folder per conversation, inherited on first use.

    The invariant that matters is the asymmetry: a new chat INHERITS the
    last-used folder so nobody re-picks one every time, but once a chat is
    bound, changing the global default must never widen what that chat can
    reach. Silently expanding an existing conversation's filesystem access
    from a settings screen would be the bug worth avoiding here.
    """

    async def test_a_new_conversation_has_no_folder(self, client):
        conv = (await client.post("/conversations")).json()
        ws = (await client.get(f"/conversations/{conv['id']}/workspace")).json()
        assert ws["root"] is None
        assert ws["bound"] is False

    async def test_setting_a_folder_binds_it(self, client, tmp_path):
        conv = (await client.post("/conversations")).json()
        await client.put(f"/conversations/{conv['id']}/workspace", json={"root": str(tmp_path)})
        ws = (await client.get(f"/conversations/{conv['id']}/workspace")).json()
        assert ws["root"] == str(tmp_path)
        assert ws["bound"] is True
        assert ws["exists"] is True

    async def test_a_later_conversation_inherits_the_last_folder(self, client, tmp_path):
        first = (await client.post("/conversations")).json()
        await client.put(f"/conversations/{first['id']}/workspace", json={"root": str(tmp_path)})

        second = (await client.post("/conversations")).json()
        ws = (await client.get(f"/conversations/{second['id']}/workspace")).json()
        assert ws["root"] == str(tmp_path)   # inherited, so no re-picking
        assert ws["bound"] is False          # but not bound: it can still diverge

    async def test_a_bound_conversation_ignores_a_later_default(self, client, tmp_path):
        # THE load-bearing case. Chat A is bound to one folder; chat B then
        # picks another, which updates the global default. A must not move.
        a = (await client.post("/conversations")).json()
        b = (await client.post("/conversations")).json()
        folder_a = tmp_path / "project-a"
        folder_b = tmp_path / "project-b"
        folder_a.mkdir()
        folder_b.mkdir()

        await client.put(f"/conversations/{a['id']}/workspace", json={"root": str(folder_a)})
        await client.put(f"/conversations/{b['id']}/workspace", json={"root": str(folder_b)})

        ws_a = (await client.get(f"/conversations/{a['id']}/workspace")).json()
        assert ws_a["root"] == str(folder_a)

    async def test_a_folder_that_is_gone_is_reported_not_forgotten(self, client, tmp_path):
        # An unplugged drive should still show as the chosen folder, flagged.
        gone = tmp_path / "removed"
        gone.mkdir()
        conv = (await client.post("/conversations")).json()
        await client.put(f"/conversations/{conv['id']}/workspace", json={"root": str(gone)})
        gone.rmdir()

        ws = (await client.get(f"/conversations/{conv['id']}/workspace")).json()
        assert ws["root"] == str(gone)   # remembered
        assert ws["exists"] is False     # but honest about it

    async def test_clearing_returns_to_inheriting(self, client, tmp_path):
        conv = (await client.post("/conversations")).json()
        await client.put(f"/conversations/{conv['id']}/workspace", json={"root": str(tmp_path)})
        await client.put(f"/conversations/{conv['id']}/workspace", json={"root": None})
        ws = (await client.get(f"/conversations/{conv['id']}/workspace")).json()
        assert ws["bound"] is False


class TestWorkspaceTree:
    # A dedicated subdirectory, NOT tmp_path itself: the `settings` fixture
    # puts arthur.db in tmp_path too, so pointing the workspace at the bare
    # tmp_path makes the app's own database show up as project files.
    @pytest.fixture
    def project(self, tmp_path):
        p = tmp_path / "project"
        p.mkdir()
        return p

    async def test_lists_files_and_folders(self, client, project):
        (project / "src").mkdir()
        (project / "src" / "app.py").write_text("x")
        (project / "README.md").write_text("y")
        conv = (await client.post("/conversations")).json()
        await client.put(f"/conversations/{conv['id']}/workspace", json={"root": str(project)})

        tree = (await client.get(f"/workspace/tree?conversation_id={conv['id']}")).json()
        names = [n["name"] for n in tree["tree"]]
        # Folders sort before files, the ordering every file browser uses.
        assert names == ["src", "README.md"]
        src = tree["tree"][0]
        assert src["dir"] is True
        assert src["children"][0]["path"] == "src/app.py"

    async def test_noise_directories_are_skipped(self, client, project):
        for junk in ("node_modules", "__pycache__", ".git"):
            (project / junk).mkdir()
            (project / junk / "f.txt").write_text("x")
        (project / "real.py").write_text("x")
        conv = (await client.post("/conversations")).json()
        await client.put(f"/conversations/{conv['id']}/workspace", json={"root": str(project)})

        tree = (await client.get(f"/workspace/tree?conversation_id={conv['id']}")).json()
        assert [n["name"] for n in tree["tree"]] == ["real.py"]

    async def test_no_folder_is_an_empty_tree_not_an_error(self, client):
        conv = (await client.post("/conversations")).json()
        resp = await client.get(f"/workspace/tree?conversation_id={conv['id']}")
        assert resp.status_code == 200
        assert resp.json() == {"root": None, "tree": [], "truncated": False}


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
