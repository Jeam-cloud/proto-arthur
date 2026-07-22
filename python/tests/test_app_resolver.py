"""open_app resolution — 'file explorer' must never end in a Windows error dialog."""

import tools.computer as computer
from tools.computer import APP_ALIASES, resolve_windows_app


class TestAliases:
    def test_human_names_map_to_windows_names(self):
        assert APP_ALIASES["file explorer"] == "explorer"
        assert APP_ALIASES["vscode"] == "code"
        assert APP_ALIASES["vs code"] == "code"
        assert APP_ALIASES["task manager"] == "taskmgr"
        assert APP_ALIASES["settings"] == "ms-settings:"


class TestResolution:
    def test_uri_kind_for_settings(self, monkeypatch):
        kind, target = resolve_windows_app("settings")
        assert kind == "uri" and target == "ms-settings:"

    def test_path_hit_resolves_to_exe(self, monkeypatch):
        monkeypatch.setattr(computer.shutil, "which",
                            lambda t: r"C:\Windows\explorer.exe" if t == "explorer" else None)
        kind, target = resolve_windows_app("File Explorer")  # case-insensitive
        assert kind == "exe" and target.endswith("explorer.exe")

    def test_start_menu_fuzzy_match(self, monkeypatch):
        monkeypatch.setattr(computer.shutil, "which", lambda t: None)
        monkeypatch.setattr(computer, "_windows_app_paths_lookup", lambda e: None)
        monkeypatch.setattr(computer, "_get_start_apps",
                            lambda: [("Spotify Music", "SpotifyAB.Spotify!App"),
                                     ("Visual Studio Code", "vscode-appid")])
        kind, target = resolve_windows_app("spotify")
        assert kind == "appid" and target == "SpotifyAB.Spotify!App"

    def test_close_typo_auto_resolves(self, monkeypatch):
        """'open blendr' should just open Blender — fuzzy match absorbs typos."""
        monkeypatch.setattr(computer.shutil, "which", lambda t: None)
        monkeypatch.setattr(computer, "_windows_app_paths_lookup", lambda e: None)
        monkeypatch.setattr(computer, "_get_start_apps",
                            lambda: [("Blender", "b"), ("Blend for Visual Studio", "bvs")])
        kind, target = resolve_windows_app("blendr")
        assert kind == "appid" and target == "b"

    def test_truly_unknown_app_fails_cleanly(self, monkeypatch):
        """Nothing matches -> (None, suggestions) — NEVER a blind launch that
        pops a Windows error dialog."""
        monkeypatch.setattr(computer.shutil, "which", lambda t: None)
        monkeypatch.setattr(computer, "_windows_app_paths_lookup", lambda e: None)
        monkeypatch.setattr(computer, "_get_start_apps",
                            lambda: [("Calculator", "calc-id")])
        kind, suggestions = resolve_windows_app("some nonexistent program xyz")
        assert kind is None
        assert isinstance(suggestions, list)

    def test_registry_hit_wins_over_start_menu(self, monkeypatch):
        monkeypatch.setattr(computer.shutil, "which", lambda t: None)
        monkeypatch.setattr(computer, "_windows_app_paths_lookup",
                            lambda e: r"C:\Program Files\App\code.exe" if e == "code" else None)
        kind, target = resolve_windows_app("vscode")
        assert kind == "exe" and target.endswith("code.exe")
