"""Workspace search.

Two properties carry the feature: it must find things (obviously), and it must
never lie about having found everything. The limit-reporting tests matter as
much as the matching ones — a silently truncated search is worse than a failed
one, because the agent acts on it with confidence.
"""

from __future__ import annotations

import pytest

from coding.changeset import ChangeSet
from core.errors import PathTraversalError
from tools.base import ToolContext
from tools.search import FindFilesTool, SearchFilesTool


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def login(user):\n    return check(user)\n")
    (tmp_path / "src" / "utils.py").write_text("def helper():\n    pass\n")
    (tmp_path / "src" / "app.test.js").write_text("test('login', () => {});\n")
    (tmp_path / "README.md").write_text("# Atlas\nRun the login flow.\n")

    # Noise that must never appear in results.
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("login login login\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "COMMIT_EDITMSG").write_text("login\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00login\x00")
    return str(tmp_path)


def ctx_for(workspace, changes=None) -> ToolContext:
    return ToolContext(conversation_id="c1", workspace_root=workspace, changes=changes)


class TestSearchFiles:
    async def test_finds_text_with_file_and_line(self, workspace):
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="def login"), ctx_for(workspace))
        assert res.ok
        assert "src/app.py:1: def login(user):" in res.content

    async def test_skips_noise_directories(self, workspace):
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="login"), ctx_for(workspace))
        assert "node_modules" not in res.content
        assert ".git" not in res.content

    async def test_skips_binary_files(self, workspace):
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="login"), ctx_for(workspace))
        assert "logo.png" not in res.content

    async def test_case_insensitive_by_default(self, workspace):
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="LOGIN"), ctx_for(workspace))
        assert res.ok and "app.py" in res.content

    async def test_case_sensitive_when_asked(self, workspace):
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="LOGIN", case_sensitive=True), ctx_for(workspace))
        assert "No matches" in res.content

    async def test_file_pattern_narrows_the_search(self, workspace):
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="login", file_pattern="*.md"), ctx_for(workspace))
        assert "README.md" in res.content and "app.py" not in res.content

    async def test_plain_query_is_escaped_not_treated_as_regex(self, workspace, tmp_path):
        """A query containing regex metacharacters is TEXT unless regex=true —
        otherwise a search for `check(user)` silently means something else."""
        (tmp_path / "src" / "cost.py").write_text("price = 'cost: $5.00 (net)'\n")
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="cost: $5.00 (net)"), ctx_for(workspace))
        assert res.ok and "cost.py" in res.content

    async def test_regex_mode_works(self, workspace):
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query=r"def \w+\(", regex=True), ctx_for(workspace))
        assert res.ok and "app.py" in res.content and "utils.py" in res.content

    async def test_invalid_regex_tells_the_model_how_to_recover(self, workspace):
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="(unclosed", regex=True), ctx_for(workspace))
        assert not res.ok and "regex=false" in res.content

    async def test_no_matches_is_ok_not_an_error(self, workspace):
        """Finding nothing is a valid answer. Returning ok=False would read as
        'the tool broke' and invite a pointless retry."""
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="zzzznotpresent"), ctx_for(workspace))
        assert res.ok and "No matches" in res.content

    async def test_traversal_blocked(self, workspace):
        with pytest.raises(PathTraversalError):
            await SearchFilesTool().execute(
                SearchFilesTool.Args(query="x", path="../.."), ctx_for(workspace))

    async def test_per_file_cap_is_reported_not_silent(self, workspace, tmp_path):
        (tmp_path / "src" / "many.py").write_text("hit\n" * 40)
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="hit", file_pattern="many.py"), ctx_for(workspace))
        assert "more matches hidden" in res.content
        assert "40 matches" in res.content   # the true total is still stated

    async def test_total_cap_is_reported(self, workspace, tmp_path):
        for i in range(40):
            (tmp_path / "src" / f"f{i}.py").write_text("needle\n" * 5)
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="needle"), ctx_for(workspace))
        assert "stopped at 100 matches" in res.content

    async def test_very_long_lines_are_truncated(self, workspace, tmp_path):
        (tmp_path / "src" / "min.js").write_text("var x=1;" + "y" * 5000 + "needle\n")
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="needle", file_pattern="min.js"), ctx_for(workspace))
        assert max(len(line) for line in res.content.splitlines()) < 400


class TestSearchOverlay:
    """Search reads through the changeset, exactly as read_file does."""

    async def test_finds_text_in_a_staged_edit(self, workspace):
        cs = ChangeSet(root=workspace)
        cs.stage_write("src/utils.py", "def helper():\n    return 'brandnewtoken'\n")
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="brandnewtoken"), ctx_for(workspace, cs))
        assert res.ok and "src/utils.py" in res.content

    async def test_finds_text_in_a_staged_new_file(self, workspace):
        cs = ChangeSet(root=workspace)
        cs.stage_write("src/brand.py", "MARKER = 1\n")
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="MARKER"), ctx_for(workspace, cs))
        assert res.ok and "src/brand.py" in res.content

    async def test_does_not_find_text_the_agent_already_removed(self, workspace):
        cs = ChangeSet(root=workspace)
        cs.stage_write("src/app.py", "def logout(user):\n    pass\n")
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="def login"), ctx_for(workspace, cs))
        assert "No matches" in res.content

    async def test_ignores_files_staged_for_deletion(self, workspace):
        cs = ChangeSet(root=workspace)
        cs.stage_delete("README.md")
        res = await SearchFilesTool().execute(
            SearchFilesTool.Args(query="login", file_pattern="*.md"), ctx_for(workspace, cs))
        assert "No matches" in res.content


class TestFindFiles:
    async def test_matches_by_extension(self, workspace):
        res = await FindFilesTool().execute(
            FindFilesTool.Args(pattern="*.py"), ctx_for(workspace))
        assert "src/app.py" in res.content and "src/utils.py" in res.content

    async def test_matches_a_full_relative_path_pattern(self, workspace):
        res = await FindFilesTool().execute(
            FindFilesTool.Args(pattern="src/*.py"), ctx_for(workspace))
        assert "src/app.py" in res.content and "README.md" not in res.content

    async def test_skips_noise_directories(self, workspace):
        res = await FindFilesTool().execute(
            FindFilesTool.Args(pattern="*.js"), ctx_for(workspace))
        assert "node_modules" not in res.content

    async def test_several_globs_in_one_pattern(self, workspace):
        """Models routinely send '*.html *.css' as a single pattern. Taken
        literally that matches a file with that exact name — nothing — and the
        empty result sends them looking somewhere else instead of fixing the
        query. Observed doing exactly that, then asking the user to search."""
        res = await FindFilesTool().execute(
            FindFilesTool.Args(pattern="*.md *.js"), ctx_for(workspace))
        assert res.ok
        assert "README.md" in res.content and "src/app.test.js" in res.content

    async def test_comma_separated_globs_work_too(self, workspace):
        res = await FindFilesTool().execute(
            FindFilesTool.Args(pattern="*.md, *.js"), ctx_for(workspace))
        assert "README.md" in res.content and "src/app.test.js" in res.content

    async def test_a_single_pattern_is_unaffected(self, workspace):
        res = await FindFilesTool().execute(
            FindFilesTool.Args(pattern="*.py"), ctx_for(workspace))
        assert "src/app.py" in res.content and "README.md" not in res.content

    async def test_no_matches_tells_it_not_to_delegate(self, workspace):
        res = await FindFilesTool().execute(
            FindFilesTool.Args(pattern="*.rs"), ctx_for(workspace))
        assert "Do NOT ask the user" in res.content

    async def test_no_matches_suggests_a_next_step(self, workspace):
        res = await FindFilesTool().execute(
            FindFilesTool.Args(pattern="*.rs"), ctx_for(workspace))
        assert res.ok and "list_files" in res.content
