"""Undo: the safety net that replaced the approval gate.

Code mode used to protect the user by asking BEFORE writing. It now writes and
keeps a way back, which moves the entire guarantee into this module — if these
tests are wrong, the app quietly destroys work. So the properties asserted here
are deliberately blunt: the old bytes come back, they survive a restart, and an
undo never overwrites something the user did themselves.
"""

import json

import pytest

from coding.changeset import ChangeSet
from coding.undo import UndoEntry, UndoStore


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "login.css").write_text(".label { background: #FE5654; }\n")
    return root


@pytest.fixture
def store(tmp_path):
    return UndoStore(tmp_path / "undo")


def apply_with_undo(store, cs, project, cid="conv1"):
    """Apply a changeset and record its undo, the way code_apply does."""
    result = cs.apply()
    store.record(cid, str(project), [
        UndoEntry(path=s["path"], before=s["before"], after=s["after"])
        for s in result["snapshots"]
    ])
    return result


class TestRoundTrip:
    def test_an_edit_can_be_put_back(self, store, project):
        original = (project / "login.css").read_text()
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", ".label { background: #1E88E5; }\n")
        apply_with_undo(store, cs, project)
        assert (project / "login.css").read_text() != original   # it really landed

        store.undo(store.latest("conv1")["id"])
        assert (project / "login.css").read_text() == original

    def test_a_created_file_is_removed_again(self, store, project):
        """`before` is None for a create, so undo means delete — not "restore
        an empty file", which would leave litter behind in the project."""
        cs = ChangeSet(root=str(project))
        cs.stage_write("new.py", "print('hi')\n")
        apply_with_undo(store, cs, project)
        assert (project / "new.py").exists()

        store.undo(store.latest("conv1")["id"])
        assert not (project / "new.py").exists()

    def test_a_deleted_file_comes_back(self, store, project):
        cs = ChangeSet(root=str(project))
        cs.stage_delete("login.css")
        apply_with_undo(store, cs, project)
        assert not (project / "login.css").exists()

        store.undo(store.latest("conv1")["id"])
        assert (project / "login.css").read_text() == ".label { background: #FE5654; }\n"

    def test_one_undo_covers_every_file_in_the_turn(self, store, project):
        """The unit people want to reverse is "what that message did", not one
        file of seven — so a whole apply is one undo entry."""
        (project / "b.py").write_text("b = 1\n")
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", "changed\n")
        cs.stage_write("b.py", "b = 2\n")
        apply_with_undo(store, cs, project)

        assert len(store.list("conv1")) == 1
        store.undo(store.latest("conv1")["id"])
        assert (project / "b.py").read_text() == "b = 1\n"
        assert (project / "login.css").read_text() == ".label { background: #FE5654; }\n"


class TestItSurvivesARestart:
    """The whole reason this is on disk and the changeset is not. An applied
    edit already touched the user's files, so the way back has to outlive the
    process — closing Arthur must not silently discard it."""

    def test_a_new_store_over_the_same_folder_can_still_undo(self, tmp_path, project):
        directory = tmp_path / "undo"
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", "blue\n")
        apply_with_undo(UndoStore(directory), cs, project)

        reopened = UndoStore(directory)     # as if the app had been restarted
        reopened.undo(reopened.latest("conv1")["id"])
        assert (project / "login.css").read_text() == ".label { background: #FE5654; }\n"

    def test_a_corrupt_snapshot_is_skipped_not_fatal(self, tmp_path, project):
        directory = tmp_path / "undo"
        store = UndoStore(directory)
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", "blue\n")
        apply_with_undo(store, cs, project)
        (directory / "99999999999999999999999.json").write_text("{not json")

        assert len(store.list("conv1")) == 1  # the good one is still offered


class TestItNeverDestroysWork:
    def test_a_file_the_user_edited_since_is_left_alone(self, store, project):
        """THE property that makes undo safe to offer automatically. Undo
        reverses ARTHUR's write; a file that no longer matches what Arthur
        wrote contains someone else's work, and restoring over it would make
        undo destructive — a contradiction in terms."""
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", "arthurs version\n")
        apply_with_undo(store, cs, project)

        (project / "login.css").write_text("MY OWN EDIT\n")   # user, in their editor
        result = store.undo(store.latest("conv1")["id"])

        assert result["skipped"] == ["login.css"]
        assert result["restored"] == []
        assert (project / "login.css").read_text() == "MY OWN EDIT\n"

    def test_a_partly_skipped_undo_stays_available_to_retry(self, store, project):
        (project / "b.py").write_text("b = 1\n")
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", "arthur css\n")
        cs.stage_write("b.py", "b = 2\n")
        apply_with_undo(store, cs, project)
        (project / "login.css").write_text("MY OWN EDIT\n")

        result = store.undo(store.latest("conv1")["id"])
        assert result["restored"] == ["b.py"] and result["skipped"] == ["login.css"]
        assert store.latest("conv1") is not None   # still there to try again

    def test_a_fully_reversed_apply_is_spent(self, store, project):
        """Otherwise the receipt keeps offering an Undo button that silently
        does nothing the second time."""
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", "blue\n")
        apply_with_undo(store, cs, project)
        store.undo(store.latest("conv1")["id"])
        assert store.latest("conv1") is None

    def test_an_unknown_id_is_refused_rather_than_guessed(self, store):
        result = store.undo("nope")
        assert result["restored"] == [] and result["error"]


class TestBookkeeping:
    def test_snapshots_are_scoped_to_their_conversation(self, tmp_path, project):
        """Two chats can be editing two different projects. Offering one chat's
        undo inside the other would apply a snapshot to a folder it was never
        taken from."""
        store = UndoStore(tmp_path / "undo")
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", "blue\n")
        apply_with_undo(store, cs, project, cid="conv1")

        assert store.latest("conv2") is None
        assert len(store.list("conv1")) == 1

    def test_only_the_last_n_applies_are_kept(self, tmp_path, project):
        store = UndoStore(tmp_path / "undo", keep=3)
        for i in range(6):
            cs = ChangeSet(root=str(project))
            cs.stage_write("login.css", f"version {i}\n")
            apply_with_undo(store, cs, project)
        assert len(store.list("conv1")) == 3

    def test_the_listing_carries_no_file_contents(self, store, project):
        """It feeds a button label. Shipping every snapshotted file to render
        "Undo — 1 file" would make a cheap poll expensive."""
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", "blue\n" * 500)
        apply_with_undo(store, cs, project)
        assert "blue" not in json.dumps(store.list("conv1"))

    def test_nothing_is_recorded_when_nothing_applied(self, store, project):
        cs = ChangeSet(root=str(project))
        apply_with_undo(store, cs, project)
        assert store.list("conv1") == []

    def test_snapshots_live_outside_the_users_project(self, store, project):
        """An undo file inside the folder being edited would show up in their
        diffs, their searches and their commits. Arthur's safety net is not
        their code."""
        cs = ChangeSet(root=str(project))
        cs.stage_write("login.css", "blue\n")
        apply_with_undo(store, cs, project)
        assert list(project.rglob("*.json")) == []
