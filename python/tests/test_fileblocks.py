"""File blocks — the primary write path in Code mode.

The evidence behind this module, from four real sessions with
qwen2.5-coder:7b: read_file / list_files / find_files were called correctly
every time, and write_file / edit_file were called ZERO times. Printing a fenced
block is what the model does instead, every time, unprompted. So the protocol
moved to meet the model.

Two properties matter more than the rest and are tested hardest:
  * a block that names a file gets saved (that is the whole point);
  * a block that is only PART of the file NEVER overwrites it — observed for
    real, where the model printed two CSS rules of an 82-line stylesheet.
"""

from __future__ import annotations

from coding.fileblocks import (
    FileBlock,
    is_excerpt,
    parse_file_blocks,
    strip_file_blocks,
)

CSS = "\n".join([
    ".label { background: #1E88E5; }",
    ".input-field { background: #EDF7FA; }",
    ".login-button { background: #1E88E5; }",
])


class TestParsing:
    def test_a_lang_and_a_path(self):
        blocks = parse_file_blocks(f"Here you go:\n```css app/static/login.css\n{CSS}\n```")
        assert [b.path for b in blocks] == ["app/static/login.css"]
        assert blocks[0].content.startswith(".label")

    def test_a_path_with_no_language(self):
        blocks = parse_file_blocks(f"```app/static/login.css\n{CSS}\n```")
        assert [b.path for b in blocks] == ["app/static/login.css"]

    def test_a_keyed_path(self):
        """`path=`, `file=` and `title=` all appear in the wild. Rejecting a
        correct-but-differently-spelled answer teaches the model nothing."""
        for info in ('css path=app/x.css', 'css file="app/x.css"', "css title='app/x.css'"):
            blocks = parse_file_blocks(f"```{info}\n{CSS}\n```")
            assert [b.path for b in blocks] == ["app/x.css"], info

    def test_a_bare_language_is_not_a_path(self):
        """THE EXPENSIVE FALSE POSITIVE. Reading ```css as a filename would
        create a file called "css" in the project root, silently."""
        assert parse_file_blocks(f"```css\n{CSS}\n```") == []
        assert parse_file_blocks("```python\nx = 1\ny = 2\n```") == []

    def test_several_files_in_one_reply(self):
        text = (f"```app/a.css\n{CSS}\n```\nand then\n```app/b.js\nconst x = 1;\n```")
        assert [b.path for b in parse_file_blocks(text)] == ["app/a.css", "app/b.js"]

    def test_an_unlabelled_block_beside_a_labelled_one_is_ignored(self):
        text = f"For example:\n```css\n.x {{}}\n```\nAnd the file:\n```app/a.css\n{CSS}\n```"
        assert [b.path for b in parse_file_blocks(text)] == ["app/a.css"]

    def test_tilde_fences_work(self):
        assert [b.path for b in parse_file_blocks(f"~~~app/a.css\n{CSS}\n~~~")] == ["app/a.css"]

    def test_a_leading_dot_slash_is_normalised(self):
        assert parse_file_blocks(f"```./app/a.css\n{CSS}\n```")[0].path == "app/a.css"

    def test_a_traversal_is_not_laundered_into_a_valid_path(self):
        """REGRESSION, and a nasty one. Cleaning with lstrip("./") takes a
        CHARACTER SET, so it ate every leading dot and slash and turned
        '../../etc/passwd' into 'etc/passwd' — not a rejected path but a
        different VALID one inside the workspace, which safe_path then had no
        reason to refuse. The traversal must survive cleaning so it can be
        caught."""
        assert parse_file_blocks(f"```../../etc/passwd\n{CSS}\n```")[0].path.startswith("..")

    def test_content_ends_with_exactly_one_newline(self):
        """The newline before the closing fence is fence syntax, not file
        content — keeping it appends a blank line on every round trip. And a
        file with no trailing newline produces a diff whose only change is
        "\\ No newline at end of file", which is noise."""
        assert parse_file_blocks(f"```app/a.css\n{CSS}\n```")[0].content == CSS + "\n"


class TestStripping:
    def test_the_saved_block_comes_off_the_screen(self):
        """It is visible as a diff, which is a better rendering of the same
        thing. Leaving it in also doubles the history replayed to the model next
        turn, which on an 8k context is real money."""
        text = f"Updated the colours:\n```app/a.css\n{CSS}\n```\nLet me know."
        out = strip_file_blocks(text)
        assert "#1E88E5" not in out
        assert "Updated the colours:" in out and "Let me know." in out

    def test_an_unlabelled_example_survives(self):
        text = "You could write:\n```css\n.x { color: red; }\n```"
        assert strip_file_blocks(text) == text

    def test_plain_prose_is_untouched(self):
        assert strip_file_blocks("Nothing to do here.") == "Nothing to do here."


class TestExcerptGuard:
    """THE DESTRUCTIVE CASE, observed: asked to recolour an 82-line stylesheet,
    the model printed the two rules it changed. Saving that as the file would
    have deleted sixty lines, including the background image the user had
    explicitly asked to keep."""

    FILE = "\n".join(f"line {i}" for i in range(40)) + "\n"

    def test_a_fragment_is_refused(self):
        block = FileBlock(path="a.css", content="line 1\nline 2\n")
        assert is_excerpt(block, self.FILE)

    def test_a_full_rewrite_is_allowed(self):
        block = FileBlock(path="a.css", content="\n".join(f"new {i}" for i in range(40)) + "\n")
        assert not is_excerpt(block, self.FILE)

    def test_a_new_file_is_whole_by_definition(self):
        assert not is_excerpt(FileBlock(path="new.py", content="x = 1\n"), None)

    def test_a_rewrite_that_legitimately_shortens_a_file_is_allowed(self):
        """Deleting a third of a file is a real edit, not an excerpt. The guard
        has to leave room for it or it becomes the thing blocking the work."""
        shorter = "\n".join(f"line {i}" for i in range(28)) + "\n"
        assert not is_excerpt(FileBlock(path="a.css", content=shorter), self.FILE)
