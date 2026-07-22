"""Email router + SMTP backend behavior — the new daily-driver path."""

from unittest.mock import MagicMock

import pytest

from tests.conftest import FakeVault
from tools.base import ToolContext
from tools.email_service import (
    EmailRouter, EmailSendTool, GraphBackend, SendArgs, SmtpImapBackend,
)

CTX = ToolContext(conversation_id="c1")


class _Graph:
    def __init__(self, connected=False):
        self.connected = connected

    def is_connected(self):
        return self.connected


async def test_unconfigured_router_has_no_backend(db, vault):
    router = EmailRouter(SmtpImapBackend(db, vault), GraphBackend(_Graph(False)))
    assert await router.backend() is None
    assert await router.is_configured() is False


async def test_send_tool_reports_not_configured_gracefully(db, vault):
    router = EmailRouter(SmtpImapBackend(db, vault), GraphBackend(_Graph(False)))
    tool = EmailSendTool(router)
    result = await tool.execute(
        SendArgs(to=["a@b.com"], subject="hi", body="test"), CTX
    )
    assert result.ok is False
    assert "Settings" in result.content  # actionable guidance, not a stack trace


async def test_smtp_configured_via_gmail_preset(db, vault):
    """Address + password alone must be enough — hosts come from the preset."""
    await db.set_setting("email_address", "rian@gmail.com")
    vault.set("email_password", "app-password-123")
    smtp = SmtpImapBackend(db, vault)
    router = EmailRouter(smtp, GraphBackend(_Graph(False)))
    assert await router.backend() is smtp
    cfg = await smtp._config()
    assert cfg["smtp_host"] == "smtp.gmail.com" and cfg["imap_host"] == "imap.gmail.com"


async def test_graph_fallback_when_smtp_unconfigured(db, vault):
    graph = GraphBackend(_Graph(True))
    router = EmailRouter(SmtpImapBackend(db, vault), graph)
    assert await router.backend() is graph


async def test_smtp_preferred_over_graph(db, vault):
    """User's explicit app-password setup wins over a lingering MS connection."""
    await db.set_setting("email_address", "rian@gmail.com")
    vault.set("email_password", "app-password-123")
    smtp = SmtpImapBackend(db, vault)
    router = EmailRouter(smtp, GraphBackend(_Graph(True)))
    assert await router.backend() is smtp


async def test_send_builds_proper_message(db, vault, monkeypatch):
    await db.set_setting("email_address", "rian@gmail.com")
    vault.set("email_password", "pw12345678")
    smtp_backend = SmtpImapBackend(db, vault)

    sent = {}
    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)
    fake_conn.send_message = lambda msg: sent.update(
        {"from": msg["From"], "to": msg["To"], "subject": msg["Subject"], "body": msg.get_content()}
    )

    import tools.email_service as es

    monkeypatch.setattr(es.smtplib, "SMTP", MagicMock(return_value=fake_conn))
    result = await smtp_backend.send(["jane@work.com"], "Running late", "Be there in 10.")

    assert "jane@work.com" in result
    assert sent["from"] == "rian@gmail.com"
    assert sent["to"] == "jane@work.com"
    assert sent["subject"] == "Running late"
    assert "Be there in 10." in sent["body"]
    fake_conn.starttls.assert_called_once()  # 587 -> STARTTLS path
    fake_conn.login.assert_called_once()


async def test_send_with_cc_and_bcc(db, vault, monkeypatch):
    await db.set_setting("email_address", "rian@gmail.com")
    vault.set("email_password", "pw12345678")
    smtp_backend = SmtpImapBackend(db, vault)

    captured = {}
    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)
    fake_conn.send_message = lambda msg: captured.update(
        {"cc": msg["Cc"], "bcc": msg["Bcc"]}
    )

    import tools.email_service as es

    monkeypatch.setattr(es.smtplib, "SMTP", MagicMock(return_value=fake_conn))
    await smtp_backend.send(["a@x.com"], "s", "b", cc=["boss@x.com"], bcc=["me@x.com"])
    assert captured["cc"] == "boss@x.com"
    assert captured["bcc"] == "me@x.com"


def test_approval_summary_shows_cc():
    tool = EmailSendTool(router=None)
    summary = tool.approval_summary(SendArgs(
        to=["jane@work.com"], subject="Q3", body="Numbers attached... kidding, no attachments yet.",
        cc=["boss@work.com"],
    ))
    assert "Cc: boss@work.com" in summary
    assert "Bcc" not in summary  # empty lists stay out of the dialog


async def test_send_tool_approval_summary_shows_full_draft():
    """The approval dialog must show what will ACTUALLY be sent."""
    tool = EmailSendTool(router=None)
    summary = tool.approval_summary(
        SendArgs(to=["jane@work.com"], subject="Lunch", body="Sushi at 1?")
    )
    assert "jane@work.com" in summary and "Lunch" in summary and "Sushi at 1?" in summary


def test_send_args_rejects_bad_email():
    with pytest.raises(ValueError):
        SendArgs(to=["not-an-email"], subject="x", body="y")


class TestAttachments:
    """The exfiltration boundary: files attach ONLY from allowed roots."""

    @pytest.fixture
    def fs(self, tmp_path):
        home = tmp_path / "home"
        (home / "Documents").mkdir(parents=True)
        (home / "Desktop").mkdir()
        (home / "Downloads").mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (home / "Documents" / "report.pdf").write_bytes(b"%PDF fake")
        (workspace / "notes.txt").write_text("notes")
        (home / ".ssh").mkdir()
        (home / ".ssh" / "id_rsa").write_text("PRIVATE KEY MATERIAL")
        return {"home": home, "workspace": str(workspace)}

    def test_relative_name_found_in_documents(self, fs):
        from tools.email_service import resolve_attachment

        p = resolve_attachment("report.pdf", fs["workspace"], home=fs["home"])
        assert p.name == "report.pdf"

    def test_workspace_file_found(self, fs):
        from tools.email_service import resolve_attachment

        assert resolve_attachment("notes.txt", fs["workspace"], home=fs["home"]).name == "notes.txt"

    def test_sensitive_path_outside_roots_blocked(self, fs):
        """~/.ssh/id_rsa is under home but NOT under an allowed root."""
        from tools.email_service import resolve_attachment

        with pytest.raises(FileNotFoundError):
            resolve_attachment(str(fs["home"] / ".ssh" / "id_rsa"), fs["workspace"], home=fs["home"])

    def test_traversal_out_of_documents_blocked(self, fs):
        from tools.email_service import resolve_attachment

        with pytest.raises(FileNotFoundError):
            resolve_attachment("../.ssh/id_rsa", fs["workspace"], home=fs["home"])

    def test_size_cap_enforced(self, fs):
        from tools.email_service import load_attachments

        big = fs["home"] / "Documents" / "big.bin"
        big.write_bytes(b"x" * 2000)
        with pytest.raises(ValueError, match="limit"):
            load_attachments(["big.bin"], fs["workspace"], limit=1000, home=fs["home"])

    def test_loaded_attachment_has_mimetype(self, fs):
        from tools.email_service import load_attachments

        [att] = load_attachments(["report.pdf"], fs["workspace"], limit=10_000, home=fs["home"])
        assert att.filename == "report.pdf" and att.mimetype == "application/pdf"

    async def test_smtp_message_carries_attachment(self, db, vault, monkeypatch, fs):
        await db.set_setting("email_address", "rian@gmail.com")
        vault.set("email_password", "pw12345678")
        smtp_backend = SmtpImapBackend(db, vault)

        captured = {}
        fake_conn = MagicMock()
        fake_conn.__enter__ = MagicMock(return_value=fake_conn)
        fake_conn.__exit__ = MagicMock(return_value=False)
        fake_conn.send_message = lambda msg: captured.update(
            {"names": [p.get_filename() for p in msg.iter_attachments()]}
        )

        import tools.email_service as es
        from tools.email_service import load_attachments

        monkeypatch.setattr(es.smtplib, "SMTP", MagicMock(return_value=fake_conn))
        atts = load_attachments(["report.pdf"], fs["workspace"], limit=10_000, home=fs["home"])
        await smtp_backend.send(["a@x.com"], "s", "b", attachments=atts)
        assert captured["names"] == ["report.pdf"]
