"""Path traversal — tracker task p5t5, as unit tests."""

import pytest

from core.errors import PathTraversalError
from tools.coding import _safe_path


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')")
    return str(tmp_path)


class TestSafePath:
    def test_normal_relative_path(self, workspace):
        p = _safe_path(workspace, "src/app.py")
        assert p.name == "app.py"

    def test_dotdot_escape_blocked(self, workspace):
        with pytest.raises(PathTraversalError):
            _safe_path(workspace, "../../etc/passwd")

    def test_deep_dotdot_blocked(self, workspace):
        with pytest.raises(PathTraversalError):
            _safe_path(workspace, "src/../../../../secrets.txt")

    def test_absolute_path_blocked(self, workspace):
        # Path("/etc/passwd") joined to root REPLACES it — resolve() lands outside -> blocked
        with pytest.raises(PathTraversalError):
            _safe_path(workspace, "/etc/passwd")

    def test_windows_absolute_blocked(self, workspace):
        with pytest.raises(PathTraversalError):
            _safe_path(workspace, "C:\\Windows\\System32\\config")

    def test_git_dir_blocked(self, workspace):
        with pytest.raises(PathTraversalError):
            _safe_path(workspace, ".git/hooks/pre-commit")

    def test_no_workspace_configured(self):
        with pytest.raises(PathTraversalError, match="No workspace folder"):
            _safe_path(None, "anything.txt")

    def test_dotdot_that_stays_inside_is_fine(self, workspace):
        p = _safe_path(workspace, "src/../src/app.py")
        assert p.name == "app.py"
