"""Applying a changeset to disk, with the undo snapshot and the receipt.

Lives in its own module because there are now TWO callers and they must not
drift apart: the chat turn applies automatically when it finishes, and the
review panel applies on a click when `code_review_before_apply` is on. If the
snapshot were written in one path and not the other, undo would silently work
in one half of the app — the worst possible shape for a safety feature.

Collaborators are passed in rather than reached for through AppState, so this
is importable from both the service layer and the routes without a cycle.
"""

from __future__ import annotations

import logging
from typing import Any

from coding.undo import UndoEntry

log = logging.getLogger(__name__)


def receipt_text(applied: list[str], conflicts: list[str], root: str | None) -> str:
    """What the transcript says about a write that actually happened.

    Names the file when there is one — "Wrote login.css" is checkable at a
    glance and "Wrote 1 file" is not, and the whole point of the receipt is to
    be the thing a fabricated summary cannot fake.
    """
    n = len(applied)
    if n == 1:
        text = f"Wrote {applied[0]} in {root or 'your folder'}."
    else:
        text = f"Wrote {n} files to {root or 'your folder'}."
    if conflicts:
        text += (f" {len(conflicts)} left alone — "
                 "changed on disk since Arthur read them.")
    return text


async def apply_changeset(
    changes,
    *,
    conversation_id: str,
    conversations,
    undos=None,
    audit=None,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Write staged edits, snapshot what they replaced, and record a receipt.

    Order matters: the snapshot is taken from the result of `apply`, which is
    the only place the previous file contents still exist — a PendingChange is
    dropped the moment it lands, taking `before` with it.

    THE ROOT COMES FROM THE CHANGESET, not from the conversation's configured
    folder. They are normally the same, but when they differ the changeset is
    the truthful one: it is the folder these paths were resolved against, and
    an undo snapshot filed under any other folder either fails to resolve or —
    far worse — resolves against the wrong project.

    A snapshot failure never fails the apply. The files are already written by
    that point, and reporting failure would leave the user believing nothing
    happened. `undo_id` comes back None instead, and the UI says so.
    """
    root = getattr(changes, "root", None)
    result = changes.apply(paths)
    snapshots = result.pop("snapshots", [])

    result["undo_id"] = None
    if snapshots and undos is not None:
        result["undo_id"] = undos.record(
            conversation_id, root,
            [UndoEntry(path=s["path"], before=s["before"], after=s["after"])
             for s in snapshots],
        )

    if audit is not None:
        # Audited because this is the moment the agent's work becomes real. If a
        # user later finds a file they did not expect to change, the trail says
        # which conversation applied it and when.
        await audit.record(
            "code.changes_applied", "info",
            conversation_id=conversation_id,
            applied=", ".join(result["applied"]),
            conflicts=", ".join(result["conflicts"]),
        )

    # A RECEIPT in the transcript.
    #
    # Written as its own message role so it is visible in the transcript but
    # NEVER replayed to the model -- history_for_model selects only 'user' and
    # 'assistant', so this is a note to the human, not a turn the model can be
    # confused by or made to imitate.
    #
    # It carries more weight now than when edits were staged. With the model
    # writing files directly, "Arthur says it edited login.css" and "login.css
    # changed" have to be the same fact, and the receipt is what makes them the
    # same fact: it is written from the apply result, so it cannot describe work
    # that did not happen.
    if result["applied"]:
        text = receipt_text(result["applied"], result["conflicts"], root)
        result["receipt"] = {
            "id": await conversations.add_message(conversation_id, "receipt", text),
            "role": "receipt",
            "content": text,
        }
    return result
