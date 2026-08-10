"""The changeset layer — the promise is "nothing touches disk until applied",
so most of these tests assert on the filesystem, not on return values."""

from __future__ import annotations

import pytest

from coding.changeset import ChangeSet, ChangeSetStore
from core.errors import PathTraversalError
from tools.base import ToolContext
from tools.coding import DeleteFileTool, EditFileTool, ListDirTool, ReadFileTool, WriteFileTool


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    return 1\n")
    return str(tmp_path)


@pytest.fixture
def cs(workspace) -> ChangeSet:
    return ChangeSet(root=workspace)


def ctx_for(cs: ChangeSet) -> ToolContext:
    return ToolContext(conversation_id="c1", workspace_root=cs.root, changes=cs)


class TestStaging:
    def test_write_does_not_touch_disk(self, cs, tmp_path):
        cs.stage_write("src/app.py", "def main():\n    return 2\n")
        assert (tmp_path / "src" / "app.py").read_text() == "def main():\n    return 1\n"
        assert cs.totals()["files"] == 1

    def test_create_is_not_on_disk_until_applied(self, cs, tmp_path):
        cs.stage_write("new.txt", "hello")
        assert not (tmp_path / "new.txt").exists()
        cs.apply()
        assert (tmp_path / "new.txt").read_text() == "hello"

    def test_apply_writes_and_clears(self, cs, tmp_path):
        cs.stage_write("src/app.py", "changed\n")
        result = cs.apply()
        assert result["applied"] == ["src/app.py"]
        assert (tmp_path / "src" / "app.py").read_text() == "changed\n"
        assert cs.is_empty()

    def test_discard_leaves_disk_untouched(self, cs, tmp_path):
        cs.stage_write("src/app.py", "changed\n")
        cs.discard()
        assert (tmp_path / "src" / "app.py").read_text() == "def main():\n    return 1\n"
        assert cs.is_empty()

    def test_partial_apply(self, cs, tmp_path):
        cs.stage_write("a.txt", "A")
        cs.stage_write("b.txt", "B")
        cs.apply(["a.txt"])
        assert (tmp_path / "a.txt").exists()
        assert not (tmp_path / "b.txt").exists()
        assert cs.paths() == ["b.txt"]

    def test_delete_is_staged_then_applied(self, cs, tmp_path):
        cs.stage_delete("src/app.py")
        assert (tmp_path / "src" / "app.py").exists()   # still there while pending
        cs.apply()
        assert not (tmp_path / "src" / "app.py").exists()

    def test_traversal_still_blocked_when_staging(self, cs):
        with pytest.raises(PathTraversalError):
            cs.stage_write("../escape.txt", "nope")


class TestOverlay:
    def test_read_returns_staged_text(self, cs):
        cs.stage_write("src/app.py", "staged\n")
        text, state = cs.read("src/app.py")
        assert (text, state) == ("staged\n", "staged")

    def test_read_falls_back_to_disk(self, cs):
        text, state = cs.read("src/app.py")
        assert state == "disk" and "return 1" in text

    def test_staged_delete_reads_as_missing(self, cs):
        cs.stage_delete("src/app.py")
        assert cs.read("src/app.py") == (None, "missing")

    def test_path_normalises_to_one_entry(self, cs):
        """`src/app.py` and `./src/app.py` are the same file, not two edits."""
        cs.stage_write("src/app.py", "one\n")
        cs.stage_write("./src/app.py", "two\n")
        assert cs.paths() == ["src/app.py"]

    def test_diff_is_against_the_original_not_the_last_stage(self, cs):
        cs.stage_write("src/app.py", "one\n")
        cs.stage_write("src/app.py", "two\n")
        diff = cs.summary()[0]["diff"]
        assert "return 1" in diff and "two" in diff

    def test_edit_back_to_original_drops_the_change(self, cs):
        original = "def main():\n    return 1\n"
        cs.stage_write("src/app.py", "changed\n")
        cs.stage_write("src/app.py", original)
        assert cs.is_empty()


class TestConflicts:
    def test_external_edit_blocks_apply_for_that_file(self, cs, tmp_path):
        cs.stage_write("src/app.py", "agent version\n")
        cs.stage_write("other.txt", "fine")
        (tmp_path / "src" / "app.py").write_text("user edited this\n")  # user's editor

        result = cs.apply()
        assert result["conflicts"] == ["src/app.py"]
        assert result["applied"] == ["other.txt"]           # one conflict blocks one file
        assert (tmp_path / "src" / "app.py").read_text() == "user edited this\n"
        assert cs.paths() == ["src/app.py"]                 # still pending, not lost


class TestConflictLifecycle:
    """A conflict has to STICK to the file (so the card can show it) and then
    CLEAR when the edit is redone — otherwise the same file conflicts forever."""

    def test_conflict_is_reported_on_the_change(self, cs, tmp_path):
        cs.stage_write("src/app.py", "agent version\n")
        (tmp_path / "src" / "app.py").write_text("user edited\n")
        cs.apply()
        assert cs.summary()[0]["conflict"] is True

    def test_restaging_adopts_the_users_version_as_the_baseline(self, cs, tmp_path):
        cs.stage_write("src/app.py", "agent version\n")
        (tmp_path / "src" / "app.py").write_text("user edited\n")
        cs.apply()

        # "Re-read and retry": the agent reads current disk and edits again.
        cs.stage_write("src/app.py", "user edited, plus agent change\n")
        assert cs.summary()[0]["conflict"] is False
        result = cs.apply()
        assert result["applied"] == ["src/app.py"]
        assert (tmp_path / "src" / "app.py").read_text() == "user edited, plus agent change\n"

    def test_clean_changes_are_not_marked_conflicted(self, cs):
        cs.stage_write("src/app.py", "fine\n")
        assert cs.summary()[0]["conflict"] is False


class TestStore:
    def test_changing_folder_drops_pending_edits(self, workspace, tmp_path):
        store = ChangeSetStore()
        a = store.get("c1", workspace)
        a.stage_write("x.txt", "x")
        other = str(tmp_path / "elsewhere")
        (tmp_path / "elsewhere").mkdir()
        b = store.get("c1", other)
        assert b.is_empty() and b is not a

    def test_conversations_are_isolated(self, workspace):
        store = ChangeSetStore()
        store.get("c1", workspace).stage_write("x.txt", "x")
        assert store.get("c2", workspace).is_empty()


class TestWrongToolForTheJob:
    """A tool error is where the model decides what to do next, so it has to say
    what went wrong PRECISELY. Observed: list_files was called on
    'app/static/login.css', got a generic "No such folder", and the model
    concluded the file did not exist — then asked the user to confirm it was
    there. The path was right and the file was right in front of it; only the
    tool was wrong."""

    async def test_a_file_passed_to_list_files_says_so(self, cs):
        res = await ListDirTool().execute(ListDirTool.Args(path="src/app.py"), ctx_for(cs))
        assert not res.ok
        assert "is a file" in res.content and "read_file" in res.content
        # It must NOT read as "missing" — that is the wrong conclusion to hand a
        # model that has already located the file.
        assert "No such" not in res.content

    async def test_a_genuinely_missing_folder_still_says_missing(self, cs):
        res = await ListDirTool().execute(ListDirTool.Args(path="nope"), ctx_for(cs))
        assert not res.ok and "No such folder" in res.content


class TestTools:
    async def test_write_tool_stages(self, cs, tmp_path):
        res = await WriteFileTool().execute(
            WriteFileTool.Args(path="new.py", content="print(1)\n"), ctx_for(cs))
        assert res.ok and not (tmp_path / "new.py").exists()
        assert cs.paths() == ["new.py"]

    async def test_write_tool_without_changeset_refuses(self, workspace, tmp_path):
        """Failing closed matters more than working: no staging must never
        degrade into writing straight to the user's disk."""
        ctx = ToolContext(conversation_id="c1", workspace_root=workspace, changes=None)
        res = await WriteFileTool().execute(
            WriteFileTool.Args(path="new.py", content="print(1)\n"), ctx)
        assert not res.ok
        assert not (tmp_path / "new.py").exists()

    async def test_read_tool_sees_its_own_pending_write(self, cs):
        await WriteFileTool().execute(
            WriteFileTool.Args(path="src/app.py", content="staged body\n"), ctx_for(cs))
        res = await ReadFileTool().execute(
            ReadFileTool.Args(path="src/app.py"), ctx_for(cs))
        assert "staged body" in res.content and "pending" in res.content

    async def test_edit_replaces_exact_snippet(self, cs):
        res = await EditFileTool().execute(
            EditFileTool.Args(path="src/app.py", old_text="return 1", new_text="return 2"),
            ctx_for(cs))
        assert res.ok
        assert cs.read("src/app.py")[0] == "def main():\n    return 2\n"

    async def test_edit_refuses_ambiguous_match(self, cs):
        cs.stage_write("dup.py", "x = 1\nx = 1\n")
        res = await EditFileTool().execute(
            EditFileTool.Args(path="dup.py", old_text="x = 1", new_text="x = 2"), ctx_for(cs))
        assert not res.ok and "2 times" in res.content
        assert cs.read("dup.py")[0] == "x = 1\nx = 1\n"  # unchanged

    async def test_edit_replace_all_allows_it(self, cs):
        cs.stage_write("dup.py", "x = 1\nx = 1\n")
        res = await EditFileTool().execute(
            EditFileTool.Args(path="dup.py", old_text="x = 1", new_text="x = 2",
                              replace_all=True), ctx_for(cs))
        assert res.ok and cs.read("dup.py")[0] == "x = 2\nx = 2\n"

    async def test_edit_missing_snippet_tells_model_how_to_recover(self, cs):
        res = await EditFileTool().execute(
            EditFileTool.Args(path="src/app.py", old_text="nonexistent", new_text="x"),
            ctx_for(cs))
        assert not res.ok and "not found" in res.content.lower()

    async def test_edit_on_missing_file_points_at_write_file(self, cs):
        res = await EditFileTool().execute(
            EditFileTool.Args(path="nope.py", old_text="a", new_text="b"), ctx_for(cs))
        assert not res.ok and "write_file" in res.content

    async def test_a_missed_read_tells_the_model_to_search_not_ask(self, cs):
        """Regression: on a miss the model guessed a second path, missed again,
        then asked the user where their CSS was — with search sitting unused.
        The recovery instruction belongs ON the error, where the decision is
        actually made."""
        res = await ReadFileTool().execute(ReadFileTool.Args(path="static/login.css"), ctx_for(cs))
        assert not res.ok
        assert "find_files" in res.content and "search_files" in res.content
        assert "do not ask" in res.content.lower()

    async def test_delete_tool_stages_only(self, cs, tmp_path):
        res = await DeleteFileTool().execute(
            DeleteFileTool.Args(path="src/app.py"), ctx_for(cs))
        assert res.ok and (tmp_path / "src" / "app.py").exists()
        assert cs.summary()[0]["kind"] == "delete"
