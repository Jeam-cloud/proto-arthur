"""Patches — the middle ground between one snippet and the whole file.

File blocks are the primary write path, and their honest weakness is that the
whole file crosses the context every time. `edit_file` covers one snippet. This
covers several changes, possibly across several files, in one call.

The property that matters most is ALL-OR-NOTHING. A half-applied patch leaves a
file in a state nobody asked for, and leaves the model reasoning about a version
that never existed.
"""

from __future__ import annotations

import pytest

from coding.changeset import ChangeSet
from coding.patches import PatchError, apply_hunks, parse_patch
from tools.base import ToolContext
from tools.coding import ApplyPatchTool

CSS = """\
.label {
    background-color: #FE5654;
    width: 40px;
}

.login-button {
    background-color: #FE5654;
    color: white;
}
"""

PATCH = """\
*** Begin Patch
*** Update File: login.css
@@
 .label {
-    background-color: #FE5654;
+    background-color: #1E88E5;
     width: 40px;
*** End Patch
"""


@pytest.fixture
def cs(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "login.css").write_text(CSS)
    return ChangeSet(root=str(root))


def ctx_for(cs):
    return ToolContext(conversation_id="c1", workspace_root=cs.root, changes=cs)


async def run(cs, patch):
    return await ApplyPatchTool().execute(ApplyPatchTool.Args(patch=patch), ctx_for(cs))


class TestParsing:
    def test_update_add_and_delete_in_one_patch(self):
        ops = parse_patch(
            "*** Begin Patch\n"
            "*** Update File: a.css\n@@\n line\n-old\n+new\n"
            "*** Add File: b.py\n+print(1)\n"
            "*** Delete File: c.txt\n"
            "*** End Patch\n"
        )
        assert [(o.kind, o.path) for o in ops] == [
            ("update", "a.css"), ("add", "b.py"), ("delete", "c.txt"),
        ]
        assert ops[1].content == "print(1)\n"

    def test_the_banners_are_optional(self):
        """Models drop them constantly. Refusing an otherwise well-formed patch
        over a missing banner is the punctuation-strictness that made tool calls
        unusable in the first place."""
        ops = parse_patch("*** Update File: a.css\n@@\n x\n-old\n+new\n")
        assert [(o.kind, o.path) for o in ops] == [("update", "a.css")]

    def test_an_empty_patch_is_refused(self):
        with pytest.raises(PatchError):
            parse_patch("   ")

    def test_prose_with_no_file_header_is_refused(self):
        with pytest.raises(PatchError):
            parse_patch("Here is my patch, I changed the colours to blue.")

    def test_an_update_with_no_changes_is_refused(self):
        with pytest.raises(PatchError):
            parse_patch("*** Update File: a.css\n")


class TestMatching:
    def test_a_hunk_replaces_exactly_its_context(self):
        out = apply_hunks(CSS, [[" .label {", "-    background-color: #FE5654;",
                                 "+    background-color: #1E88E5;", "     width: 40px;"]], "a")
        assert "#1E88E5" in out
        assert out.count("#FE5654") == 1     # the button's copy is untouched

    def test_an_ambiguous_hunk_is_refused(self):
        """#FE5654 appears twice. Applying to the first match would silently
        change the wrong one — the failure fuzzy patching is famous for."""
        with pytest.raises(PatchError, match="matches 2 places"):
            apply_hunks(CSS, [["-    background-color: #FE5654;",
                               "+    background-color: #1E88E5;"]], "a")

    def test_context_that_does_not_exist_is_refused(self):
        with pytest.raises(PatchError, match="does not match"):
            apply_hunks(CSS, [[" .nonexistent {", "-  color: red;", "+  color: blue;"]], "a")

    def test_a_hunk_with_only_additions_is_refused(self):
        """Nothing to anchor to means nothing decides WHERE it goes."""
        with pytest.raises(PatchError, match="nothing to match"):
            apply_hunks(CSS, [["+    margin: 0;"]], "a")


class TestTheTool:
    async def test_a_good_patch_stages_the_change(self, cs):
        res = await run(cs, PATCH)
        assert res.ok
        assert "#1E88E5" in cs.read("login.css")[0]

    async def test_nothing_touches_disk(self, cs, tmp_path):
        await run(cs, PATCH)
        assert "#FE5654" in (tmp_path / "proj" / "login.css").read_text()

    async def test_one_bad_hunk_abandons_the_whole_patch(self, cs):
        """ALL-OR-NOTHING. A patch that half-applies leaves the file in a state
        nobody asked for, and the model then reasons about a version that never
        existed. The first file here is perfectly valid; it must still not be
        staged."""
        two_files = (
            "*** Begin Patch\n"
            "*** Update File: login.css\n@@\n .label {\n"
            "-    background-color: #FE5654;\n+    background-color: #1E88E5;\n"
            "     width: 40px;\n"
            "*** Update File: login.css\n@@\n-does not exist anywhere\n+x\n"
            "*** End Patch\n"
        )
        res = await run(cs, two_files)
        assert not res.ok
        assert cs.is_empty()
        assert "Nothing was changed" in res.content

    async def test_a_missing_file_says_so_and_stages_nothing(self, cs):
        res = await run(cs, "*** Update File: nope.css\n@@\n-a\n+b\n")
        assert not res.ok and cs.is_empty()
        assert "no such file" in res.content.lower()

    async def test_adding_over_an_existing_file_is_refused(self, cs):
        res = await run(cs, "*** Add File: login.css\n+overwritten\n")
        assert not res.ok and cs.is_empty()
        assert "already exists" in res.content

    async def test_add_and_delete_stage_correctly(self, cs):
        res = await run(cs, "*** Add File: new.py\n+print(1)\n*** Delete File: login.css\n")
        assert res.ok
        assert sorted(cs.paths()) == ["login.css", "new.py"]
        assert cs.read("new.py")[0] == "print(1)\n"
        assert cs.read("login.css")[1] == "missing"     # staged for deletion

    async def test_a_path_outside_the_folder_is_refused(self, cs):
        res = await run(cs, "*** Update File: ../../etc/passwd\n@@\n-a\n+b\n")
        assert not res.ok and cs.is_empty()

    async def test_without_a_changeset_it_refuses_rather_than_writing(self, tmp_path):
        """Failing closed: no staging must never degrade into touching disk."""
        ctx = ToolContext(conversation_id="c1", workspace_root=str(tmp_path), changes=None)
        res = await ApplyPatchTool().execute(ApplyPatchTool.Args(patch=PATCH), ctx)
        assert not res.ok
