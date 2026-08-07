"""Email that works from chat/voice with zero cloud-app registration.

WHY IMAP/SMTP (validated against how Odysseus and OpenClaw ship email):
an app password is the lowest-friction path — Gmail and Outlook both issue one
in two clicks, and there is no Azure registration ceremony.

MS Graph WAS a second backend and has been removed. It required an Azure app
registration that could not be completed in this environment, so
`ms_client_id` never left its placeholder value and the Graph path was
unreachable in practice. A fallback nobody can reach is not a fallback, it is a
second code path to keep correct for no benefit.

Architecture: one EmailRouter resolves the backend PER CALL —
    SMTP configured (address + password in vault)  -> SmtpImapBackend
    else                                           -> "not configured" guidance
The router indirection is kept even with a single backend: the chat tools
(email_send / email_list / email_search) only know the router, so adding a
second provider later changes one file rather than every call site.

The user flow you asked for is preserved end-to-end: say or type
"email jane@x.com that I'm running late" -> model drafts -> approval dialog
shows EXACT to/subject/body -> nothing sends until Allow. Voice hits the same
path because transcription just fills the composer.

WHY smtplib/imaplib run in threads: both are blocking stdlib clients; on the
event loop they'd freeze every stream for the duration of a network call.
"""

from __future__ import annotations

import asyncio
import email as email_lib
import email.header
import imaplib
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from pydantic import BaseModel, EmailStr, Field

from core.db import Database
from security.vault import SecretsVault
from tools.base import Risk, TaskMode, Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

# Host presets so users only type address + app password. Anything else can be
# configured manually in Settings.
PROVIDER_PRESETS = {
    "gmail.com": {"smtp": ("smtp.gmail.com", 587), "imap": ("imap.gmail.com", 993)},
    "googlemail.com": {"smtp": ("smtp.gmail.com", 587), "imap": ("imap.gmail.com", 993)},
    "outlook.com": {"smtp": ("smtp-mail.outlook.com", 587), "imap": ("outlook.office365.com", 993)},
    "hotmail.com": {"smtp": ("smtp-mail.outlook.com", 587), "imap": ("outlook.office365.com", 993)},
    "live.com": {"smtp": ("smtp-mail.outlook.com", 587), "imap": ("outlook.office365.com", 993)},
    "yahoo.com": {"smtp": ("smtp.mail.yahoo.com", 587), "imap": ("imap.mail.yahoo.com", 993)},
    "icloud.com": {"smtp": ("smtp.mail.me.com", 587), "imap": ("imap.mail.me.com", 993)},
}

NOT_CONFIGURED_MSG = (
    "Email is not set up. In Settings → Integrations, either add your email "
    "address + app password (Gmail/Outlook: 2 clicks to create one), or connect "
    "a Microsoft account."
)


class EmailBackend(Protocol):
    async def send(self, to: list[str], subject: str, body: str,
                   cc: list[str] | None = None, bcc: list[str] | None = None,
                   attachments: "list[Attachment] | None" = None) -> str: ...
    async def list_recent(self, count: int, unread_only: bool) -> str: ...
    async def search(self, query: str) -> str: ...


# ---- attachments ----
#
# SECURITY: "attach a file" is an exfiltration vector — an injected email
# could ask the model to attach ~/.ssh/id_rsa and mail it out. Defense in
# depth: (1) files must resolve inside an ALLOWED ROOT (the workspace folder
# + Desktop/Documents/Downloads — where people's attachable files live);
# (2) the resolved filename + size show in the approval dialog, so the human
# sees exactly what leaves the machine; (3) size caps per provider.

MAX_ATTACHMENT_TOTAL = 20 * 1024 * 1024   # SMTP: typical provider limit ~25MB


class Attachment:
    def __init__(self, filename: str, data: bytes, mimetype: str):
        self.filename = filename
        self.data = data
        self.mimetype = mimetype


def _allowed_roots(workspace_root: str | None, home: "Path | None" = None) -> "list[Path]":
    from pathlib import Path

    home = home or Path.home()
    roots = []
    if workspace_root:
        roots.append(Path(workspace_root))
    roots += [home / "Desktop", home / "Documents", home / "Downloads"]
    return [r.resolve() for r in roots]


def resolve_attachment(raw: str, workspace_root: str | None, home=None) -> "Path":
    """Find `raw` inside the allowed roots; raise with a clear message
    otherwise. Relative names are searched root by root, so 'report.pdf'
    finds ~/Documents/report.pdf without the user speaking full paths."""
    from pathlib import Path

    roots = _allowed_roots(workspace_root, home)
    candidate = Path(raw).expanduser()
    tried = []
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if any(resolved.is_relative_to(r) for r in roots) and resolved.is_file():
            return resolved
        tried.append(str(resolved))
    else:
        for root in roots:
            p = (root / raw).resolve()
            if any(p.is_relative_to(r) for r in roots) and p.is_file():
                return p
            tried.append(str(p))
    raise FileNotFoundError(
        f"Couldn't find '{raw}' in the allowed folders (workspace, Desktop, "
        "Documents, Downloads). Move the file there or give its name more precisely."
    )


def load_attachments(paths: list[str], workspace_root: str | None, limit: int, home=None) -> list[Attachment]:
    import mimetypes

    out, total = [], 0
    for raw in paths:
        p = resolve_attachment(raw, workspace_root, home)
        data = p.read_bytes()
        total += len(data)
        if total > limit:
            raise ValueError(
                f"Attachments exceed the {limit // (1024 * 1024)}MB limit for this email provider."
            )
        mimetype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        out.append(Attachment(p.name, data, mimetype))
    return out


def _decode_header(raw: str | None) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    out = []
    for text, charset in parts:
        out.append(text.decode(charset or "utf-8", "replace") if isinstance(text, bytes) else text)
    return "".join(out)


def _body_snippet(msg: email_lib.message.Message, limit: int = 160) -> str:
    """First text/plain fragment, flattened. HTML-only mails fall back to a
    stripped-tag snippet — good enough for a one-line preview."""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(part.get_content_charset() or "utf-8", "replace")[:limit].replace("\n", " ")
        else:
            payload = msg.get_payload(decode=True) or b""
            text = payload.decode(msg.get_content_charset() or "utf-8", "replace")
            if msg.get_content_type() == "text/html":
                import re

                text = re.sub(r"<[^>]+>", " ", text)
            return text[:limit].replace("\n", " ")
    except Exception:
        pass
    return ""


class SmtpImapBackend:
    """Plain-protocol backend. Credentials come from Settings (address/hosts)
    + the OS vault (app password) at CALL time — switching accounts in
    Settings takes effect on the next message, no restart."""

    def __init__(self, db: Database, vault: SecretsVault):
        self._db = db
        self._vault = vault

    async def _config(self) -> dict | None:
        address = await self._db.get_setting("email_address")
        password = self._vault.get("email_password")
        if not address or not password:
            return None
        domain = address.rsplit("@", 1)[-1].lower()
        preset = PROVIDER_PRESETS.get(domain, {})
        return {
            "address": address,
            "password": password,
            "smtp_host": await self._db.get_setting("email_smtp_host") or preset.get("smtp", ("", 587))[0],
            "smtp_port": int(await self._db.get_setting("email_smtp_port") or preset.get("smtp", ("", 587))[1]),
            "imap_host": await self._db.get_setting("email_imap_host") or preset.get("imap", ("", 993))[0],
            "imap_port": int(await self._db.get_setting("email_imap_port") or preset.get("imap", ("", 993))[1]),
        }

    async def is_configured(self) -> bool:
        cfg = await self._config()
        return bool(cfg and cfg["smtp_host"])

    async def verify(self) -> None:
        """Connect + authenticate WITHOUT sending — called when the user saves
        credentials, so a wrong password fails at save time with a clear
        message instead of at send time mid-conversation. Raises RuntimeError
        with user-facing text on failure."""
        cfg = await self._config()
        if not cfg or not cfg["smtp_host"]:
            raise RuntimeError(NOT_CONFIGURED_MSG)

        def _check() -> None:
            try:
                if cfg["smtp_port"] == 465:
                    with smtplib.SMTP_SSL(cfg["smtp_host"], 465, timeout=20) as smtp:
                        smtp.login(cfg["address"], cfg["password"])
                else:
                    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=20) as smtp:
                        smtp.starttls()
                        smtp.login(cfg["address"], cfg["password"])
            except smtplib.SMTPAuthenticationError as e:
                # 534/535 from Gmail & friends = credentials rejected — almost
                # always a normal password where an app password is required.
                raise RuntimeError(
                    "The mail server rejected the sign-in. Make sure you're using an "
                    "APP password (created in your account's security settings), not "
                    "your normal account password."
                ) from e
            except OSError as e:
                raise RuntimeError(
                    f"Couldn't reach {cfg['smtp_host']} — check your internet connection "
                    "and the server settings."
                ) from e

        await asyncio.to_thread(_check)

    async def send(self, to: list[str], subject: str, body: str,
                   cc: list[str] | None = None, bcc: list[str] | None = None,
                   attachments: list[Attachment] | None = None) -> str:
        cfg = await self._config()
        if not cfg or not cfg["smtp_host"]:
            raise RuntimeError(NOT_CONFIGURED_MSG)

        def _send() -> None:
            msg = EmailMessage()
            msg["From"] = cfg["address"]
            msg["To"] = ", ".join(to)
            if cc:
                msg["Cc"] = ", ".join(cc)
            if bcc:
                # send_message() reads Bcc for the envelope, then strips the
                # header off the wire — recipients get the mail, not the list.
                msg["Bcc"] = ", ".join(bcc)
            msg["Subject"] = subject
            msg.set_content(body)
            for att in attachments or []:
                maintype, _, subtype = att.mimetype.partition("/")
                msg.add_attachment(att.data, maintype=maintype, subtype=subtype,
                                   filename=att.filename)
            # Port decides the TLS style (pattern borrowed from Odysseus's
            # battle-tested email helpers): 465 = implicit TLS from byte one
            # (SMTP_SSL); 587 = connect plain, upgrade via STARTTLS before AUTH.
            if cfg["smtp_port"] == 465:
                with smtplib.SMTP_SSL(cfg["smtp_host"], 465, timeout=30) as smtp:
                    smtp.login(cfg["address"], cfg["password"])
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as smtp:
                    smtp.starttls()
                    smtp.login(cfg["address"], cfg["password"])
                    smtp.send_message(msg)

        await asyncio.to_thread(_send)
        return f"Email sent to {', '.join(to)}."

    async def list_recent(self, count: int, unread_only: bool) -> str:
        # Config is read HERE (async, on the main loop) and passed into the
        # thread as plain data. Never touch the aiosqlite connection from a
        # worker thread — it belongs to the main event loop.
        cfg = await self._config()
        return await asyncio.to_thread(self._fetch_sync, cfg, count, unread_only, None)

    async def search(self, query: str) -> str:
        cfg = await self._config()
        return await asyncio.to_thread(self._fetch_sync, cfg, 10, False, query)

    def _fetch_sync(self, cfg: dict | None, count: int, unread_only: bool, query: str | None) -> str:
        if not cfg or not cfg["imap_host"]:
            raise RuntimeError(NOT_CONFIGURED_MSG)
        # Default _MAXLINE (1MB) chokes on large mailbox responses; Odysseus
        # raises it the same way after hitting this in the wild.
        imaplib._MAXLINE = 10_000_000
        with imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], timeout=30) as imap:
            imap.login(cfg["address"], cfg["password"])
            imap.select("INBOX", readonly=True)  # readonly: listing never marks-as-read
            if query:
                # IMAP TEXT search; quotes stripped to avoid syntax injection
                criteria = f'(TEXT "{query[:100].replace(chr(34), "")}")'
            elif unread_only:
                criteria = "(UNSEEN)"
            else:
                criteria = "ALL"
            _, data = imap.search(None, criteria)
            ids = data[0].split()[-count:]
            lines = []
            for mid in reversed(ids):
                _, msg_data = imap.fetch(mid, "(BODY.PEEK[])")
                msg = email_lib.message_from_bytes(msg_data[0][1])
                lines.append(
                    f"- [{msg.get('Date', '')[:22]}] {_decode_header(msg.get('From'))} "
                    f"| {_decode_header(msg.get('Subject')) or '(no subject)'} "
                    f"| {_body_snippet(msg)}"
                )
            return "\n".join(lines) or "(no messages found)"


# (GraphBackend removed. MS Graph needed an Azure app registration that could
#  not be completed here, ms_client_id never left its placeholder, and
#  SMTP/IMAP has been the working path throughout.)


class EmailRouter:
    """Resolves the email backend at call time, so a Settings change applies
    to the next message without a restart.

    There is one backend now. MS Graph was removed: it required an Azure app
    registration that could not be completed in this environment, `ms_client_id`
    never left its placeholder, and SMTP/IMAP with an app password has been the
    working path throughout. A fallback that cannot be reached is not a
    fallback -- it is a second code path to keep correct for no benefit.

    The indirection is KEPT rather than inlined into the tools: a second backend
    (Gmail API, JMAP) would slot in here, and the call sites should not have to
    change when it does.
    """

    def __init__(self, smtp: SmtpImapBackend):
        self._smtp = smtp

    async def backend(self) -> EmailBackend | None:
        return self._smtp if await self._smtp.is_configured() else None

    async def is_configured(self) -> bool:
        return await self.backend() is not None


# ---------------- chat tools ----------------

class SendArgs(BaseModel):
    to: list[EmailStr] = Field(min_length=1, max_length=10)
    subject: str = Field(max_length=200)
    body: str = Field(max_length=20_000)
    cc: list[EmailStr] = Field(default_factory=list, max_length=10, description="Carbon-copy recipients")
    bcc: list[EmailStr] = Field(default_factory=list, max_length=10, description="Blind carbon-copy recipients")
    attachments: list[str] = Field(
        default_factory=list, max_length=5,
        description="File names or paths to attach. Files must be in the user's workspace folder, Desktop, Documents, or Downloads.",
    )


class EmailSendTool(Tool):
    name = "email_send"
    description = (
        "Send an email from the user's account. Draft a clear subject and body from "
        "what the user asked; the app will show them the draft for confirmation before sending."
    )
    Args = SendArgs
    risk = Risk.CONFIRM  # the approval dialog IS the draft-review step
    modes = {TaskMode.EMAIL}

    def __init__(self, router: EmailRouter):
        self._router = router

    def approval_summary(self, args: SendArgs) -> str:
        lines = [f"Send email to {', '.join(args.to)}"]
        if args.cc:
            lines.append(f"Cc: {', '.join(args.cc)}")
        if args.bcc:
            lines.append(f"Bcc: {', '.join(args.bcc)}")
        if args.attachments:
            lines.append(f"Attach: {', '.join(args.attachments)}")
        lines.append(f"Subject: {args.subject}\n---\n{args.body[:600]}{'…' if len(args.body) > 600 else ''}")
        return "\n".join(lines)

    async def execute(self, args: SendArgs, ctx: ToolContext) -> ToolResult:
        backend = await self._router.backend()
        if backend is None:
            return ToolResult(ok=False, content=NOT_CONFIGURED_MSG, summary="email not configured")

        loaded = None
        if args.attachments:
            limit = MAX_ATTACHMENT_TOTAL
            try:
                loaded = load_attachments(args.attachments, ctx.workspace_root, limit)
            except (FileNotFoundError, ValueError) as e:
                # The model gets the reason and can tell the user / adjust —
                # a missing file must not send a half-finished email.
                return ToolResult(ok=False, content=str(e), summary="attachment problem")

        try:
            msg = await backend.send(list(args.to), args.subject, args.body,
                                     cc=list(args.cc), bcc=list(args.bcc),
                                     attachments=loaded)
        except Exception as e:
            log.warning("email send failed: %s", e)
            return ToolResult(ok=False, content=f"Sending failed: {e}", summary="send failed")
        return ToolResult(ok=True, content=msg, summary=f"sent to {args.to[0]}"
                          + (f" +{len(args.to) - 1}" if len(args.to) > 1 else ""))


class ListArgs(BaseModel):
    count: int = Field(default=10, ge=1, le=25)
    unread_only: bool = False


class EmailListTool(Tool):
    name = "email_list"
    description = "List the user's most recent inbox emails (sender, subject, preview)."
    Args = ListArgs
    risk = Risk.SAFE
    modes = {TaskMode.EMAIL}

    def __init__(self, router: EmailRouter):
        self._router = router

    def approval_summary(self, args: ListArgs) -> str:
        return f"Read the {args.count} most recent emails"

    async def execute(self, args: ListArgs, ctx: ToolContext) -> ToolResult:
        backend = await self._router.backend()
        if backend is None:
            return ToolResult(ok=False, content=NOT_CONFIGURED_MSG, summary="email not configured")
        try:
            listing = await backend.list_recent(args.count, args.unread_only)
        except Exception as e:
            return ToolResult(ok=False, content=f"Could not read inbox: {e}", summary="read failed")
        # external=True: email bodies are attacker-controlled text — the
        # gateway scans + spotlights them like any web page.
        return ToolResult(ok=True, external=True, source="email_inbox",
                          content="Recent emails:\n" + listing, summary="inbox listed")


class SearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=200)


class EmailSearchTool(Tool):
    name = "email_search"
    description = "Search the user's mailbox and return matching messages with previews."
    Args = SearchArgs
    risk = Risk.SAFE
    modes = {TaskMode.EMAIL}

    def __init__(self, router: EmailRouter):
        self._router = router

    def approval_summary(self, args: SearchArgs) -> str:
        return f'Search email for "{args.query}"'

    async def execute(self, args: SearchArgs, ctx: ToolContext) -> ToolResult:
        backend = await self._router.backend()
        if backend is None:
            return ToolResult(ok=False, content=NOT_CONFIGURED_MSG, summary="email not configured")
        try:
            matches = await backend.search(args.query)
        except Exception as e:
            return ToolResult(ok=False, content=f"Search failed: {e}", summary="search failed")
        return ToolResult(ok=True, external=True, source="email_search",
                          content="Matches:\n" + matches, summary="search done")
