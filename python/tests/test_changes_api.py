"""The review routes over the real app: /changes, /changes/apply, /changes/discard.

These exist separately from test_changeset.py because the guarantee being
checked is different. There, "staging does not touch disk". Here, "the only
route that writes to disk is the one the user clicks Apply on, and it needs a
token to reach".
"""

import httpx
import pytest

from core.app import create_app


@pytest.fixture
async def client(settings, app_state):
    app = create_app(settings=settings, state=app_state)
    async with httpx.ASGITransport(app=app) as transport:
        app.state.arthur = app_state
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer test-token-123"},
        ) as c:
            yield c


@pytest.fixture
async def anon(settings, app_state):
    """Same app, no token — these routes write to the user's disk, so 'is it
    behind auth' is a property worth asserting, not assuming."""
    app = create_app(settings=settings, state=app_state)
    async with httpx.ASGITransport(app=app) as transport:
        app.state.arthur = app_state
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            yield c


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("x = 1\n")
    return root


async def new_conversation(client) -> str:
    res = await client.post("/conversations", json={"title": "code"})
    return res.json()["id"]


def stage(app_state, cid, root, path, content):
    app_state.changesets.get(cid, str(root)).stage_write(path, content)


class TestConversationIdentity:
    """Mode and folder are properties of the conversation, decided at creation.
    Before this, mode lived in React state (so a reload made every chat General)
    and the folder fell through to a global 'last used' (so two projects at once
    was impossible)."""

    async def test_mode_and_folder_are_set_at_creation(self, client, project):
        res = await client.post("/conversations",
                                json={"mode": "code", "workspace_root": str(project)})
        assert res.json()["mode"] == "code"
        cid = res.json()["id"]
        ws = (await client.get(f"/conversations/{cid}/workspace")).json()
        assert ws["root"] == str(project) and ws["bound"] is True

    async def test_two_chats_can_hold_two_different_projects(self, client, tmp_path):
        a, b = tmp_path / "alpha", tmp_path / "beta"
        a.mkdir()
        b.mkdir()
        ca = (await client.post("/conversations",
                                json={"mode": "code", "workspace_root": str(a)})).json()["id"]
        cb = (await client.post("/conversations",
                                json={"mode": "code", "workspace_root": str(b)})).json()["id"]
        # Creating the second must not move the first — the bug that made
        # multi-project work impossible was exactly this leaking through a
        # single global setting.
        assert (await client.get(f"/conversations/{ca}/workspace")).json()["root"] == str(a)
        assert (await client.get(f"/conversations/{cb}/workspace")).json()["root"] == str(b)

    async def test_a_bodyless_post_still_makes_a_general_chat(self, client):
        res = await client.post("/conversations")
        assert res.status_code == 200 and res.json()["mode"] == "general"

    async def test_recent_folders_are_most_recent_first_and_deduped(self, client, tmp_path):
        a, b = tmp_path / "alpha", tmp_path / "beta"
        a.mkdir()
        b.mkdir()
        for root in (a, b, a):
            await client.post("/conversations",
                              json={"mode": "code", "workspace_root": str(root)})
        recents = (await client.get("/workspace/recents")).json()["recents"]
        assert [r["root"] for r in recents] == [str(a), str(b)]
        assert all(r["exists"] for r in recents)

    async def test_a_folder_that_moved_is_kept_but_flagged(self, client, tmp_path):
        gone = tmp_path / "gone"
        gone.mkdir()
        await client.post("/conversations",
                          json={"mode": "code", "workspace_root": str(gone)})
        gone.rmdir()
        recents = (await client.get("/workspace/recents")).json()["recents"]
        assert recents[0]["root"] == str(gone) and recents[0]["exists"] is False


class TestListChanges:
    async def test_empty_for_a_fresh_conversation(self, client):
        cid = await new_conversation(client)
        res = await client.get(f"/conversations/{cid}/changes")
        assert res.status_code == 200
        assert res.json() == {"changes": [], "files": 0, "additions": 0, "deletions": 0}

    async def test_lists_staged_diff(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        body = (await client.get(f"/conversations/{cid}/changes")).json()
        assert body["files"] == 1 and body["additions"] == 1 and body["deletions"] == 1
        assert "x = 2" in body["changes"][0]["diff"]

    async def test_diffs_false_omits_the_diff_body(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        body = (await client.get(f"/conversations/{cid}/changes?diffs=false")).json()
        assert "diff" not in body["changes"][0]
        assert body["files"] == 1  # counts still arrive

    async def test_requires_auth(self, anon):
        res = await anon.get("/conversations/anything/changes")
        assert res.status_code == 401


class TestApply:
    async def test_apply_writes_to_disk(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        assert (project / "app.py").read_text() == "x = 1\n"   # untouched while pending

        res = await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        assert res.json()["applied"] == ["app.py"]
        assert (project / "app.py").read_text() == "x = 2\n"

    async def test_apply_subset_leaves_the_rest_pending(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "a.py", "A")
        stage(app_state, cid, project, "b.py", "B")
        res = await client.post(f"/conversations/{cid}/changes/apply", json={"paths": ["a.py"]})
        assert res.json()["applied"] == ["a.py"] and res.json()["remaining"] == 1
        assert (project / "a.py").exists() and not (project / "b.py").exists()

    async def test_apply_is_audited(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        kinds = [e["kind"] for e in await app_state.audit.recent()]
        assert "code.changes_applied" in kinds

    async def test_apply_writes_a_receipt_into_the_transcript(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        res = await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        # NAMES the file rather than counting it. "Wrote 1 file" is not
        # checkable at a glance; "Wrote app.py" is, and being checkable is the
        # entire job of a receipt now that edits land without being approved.
        assert "app.py" in res.json()["receipt"]["content"]

        rows = (await client.get(f"/conversations/{cid}/messages")).json()
        assert [r["role"] for r in rows] == ["receipt"]

    async def test_receipt_is_never_replayed_to_the_model(self, client, app_state, project):
        """It is a note to the human. If it reached the prompt the model would
        start narrating disk writes it did not make."""
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        history = await app_state.conversations.history_for_model(cid)
        assert history == []

    async def test_no_receipt_when_nothing_applied(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        (project / "app.py").write_text("user edited\n")   # conflict: nothing applies
        res = await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        assert "receipt" not in res.json()

    async def test_apply_with_nothing_staged_is_a_no_op(self, client):
        cid = await new_conversation(client)
        res = await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        assert res.json()["applied"] == []


class TestUndoRoutes:
    """With edits landing automatically, these routes ARE the safety model —
    what the Apply button used to be, moved to the far side of the write."""

    async def test_the_button_undoes_the_last_apply_without_being_told_which(
        self, client, app_state, project,
    ):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})

        res = await client.post(f"/conversations/{cid}/undo", json={})
        print("STATUS", res.status_code, res.json())
        assert res.json()["restored"] == ["app.py"]
        assert (project / "app.py").read_text() == "x = 1\n"

    async def test_an_apply_advertises_its_undo_id(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        res = await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        assert res.json()["undo_id"]

    async def test_listing_shows_what_can_still_be_put_back(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        body = (await client.get(f"/conversations/{cid}/undo")).json()
        assert body["latest"]["files"] == ["app.py"]

    async def test_undo_writes_its_own_receipt(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        res = await client.post(f"/conversations/{cid}/undo", json={})
        assert "app.py" in res.json()["receipt"]["content"]

    async def test_undo_is_audited(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        await client.post(f"/conversations/{cid}/changes/apply", json={"paths": None})
        await client.post(f"/conversations/{cid}/undo", json={})
        kinds = [e["kind"] for e in await app_state.audit.recent()]
        assert "code.changes_undone" in kinds

    async def test_another_chats_snapshot_cannot_be_undone_here(
        self, client, app_state, project,
    ):
        """An id is a client-supplied string, and a snapshot taken in another
        conversation was taken against another folder."""
        owner = await new_conversation(client)
        stage(app_state, owner, project, "app.py", "x = 2\n")
        applied = await client.post(f"/conversations/{owner}/changes/apply", json={"paths": None})
        stolen = applied.json()["undo_id"]

        other = await new_conversation(client)
        res = await client.post(f"/conversations/{other}/undo", json={"id": stolen})
        assert res.json()["restored"] == [] and res.json()["error"]
        assert (project / "app.py").read_text() == "x = 2\n"   # untouched

    async def test_nothing_to_undo_says_so_plainly(self, client):
        cid = await new_conversation(client)
        res = await client.post(f"/conversations/{cid}/undo", json={})
        assert res.json()["restored"] == [] and res.json()["error"]

    async def test_requires_auth(self, anon):
        res = await anon.post("/conversations/anything/undo", json={})
        assert res.status_code == 401


class TestDiscard:
    async def test_discard_leaves_disk_untouched(self, client, app_state, project):
        cid = await new_conversation(client)
        stage(app_state, cid, project, "app.py", "x = 2\n")
        res = await client.post(f"/conversations/{cid}/changes/discard", json={"paths": None})
        assert res.json()["discarded"] == ["app.py"]
        assert (project / "app.py").read_text() == "x = 1\n"
        assert (await client.get(f"/conversations/{cid}/changes")).json()["files"] == 0
