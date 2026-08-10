"""Asking the user a question — the move that isn't guessing or stalling.

WHY THIS EXISTS
---------------
Given a request it cannot pin down ("clean up my code", "make it look better"),
a model has had exactly two options in Arthur: guess, or write a paragraph
asking a question the app has no way to collect an answer to. Both are bad, and
the second is worse, because the turn ends looking like an answer.

Everything else in the loop pushes hard AGAINST asking: the capability note says
do not ask permission, do not describe a step, take the next action. That is
right for permission and wrong for information — "which of these three login
pages did you mean?" is not hesitation, it is the cheapest possible way to avoid
doing the wrong work. The distinction only becomes real when there is a
mechanism, so this is the mechanism.

WHY IT ENDS THE TURN
--------------------
The model has nothing useful to do until the person answers. A model that keeps
working after asking is a model that answered its own question — which is the
original failure in a politer costume.

Structured on purpose: a question with 2-6 labelled options renders as buttons,
so answering costs one click instead of a typed sentence, and the answer comes
back as ordinary user text the model already knows how to read.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from tools.base import Risk, TaskMode, Tool, ToolContext, ToolResult

# Six is the point past which a list of buttons stops being easier than typing.
MAX_OPTIONS = 6


class Option(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=200)


class AskUserArgs(BaseModel):
    question: str = Field(min_length=3, max_length=300)
    options: list[Option] = Field(min_length=2, max_length=MAX_OPTIONS)
    multi: bool = Field(
        default=False, description="Allow picking more than one option",
    )

    @field_validator("options")
    @classmethod
    def _distinct(cls, options: list[Option]) -> list[Option]:
        """Two identical labels render as two identical buttons, which is not a
        choice. Rejected rather than deduped so the model is told, and asks a
        real question on the retry instead of silently offering one option."""
        seen = {o.label.strip().casefold() for o in options}
        if len(seen) != len(options):
            raise ValueError("options must have distinct labels")
        return options


class AskUserTool(Tool):
    name = "ask_user"
    description = (
        "Ask the user a short multiple-choice question when their request is "
        "genuinely ambiguous and guessing would waste their time. Give 2-6 "
        "concrete options. Use this INSTEAD of asking in prose — a question in "
        "your reply cannot be answered, this one can. Do not use it to ask "
        "permission for something you can just do."
    )
    Args = AskUserArgs
    # SAFE: it touches nothing. The turn stopping is not a risk, it is the point.
    risk = Risk.SAFE
    # Every mode. Ambiguity is not a property of Code mode, and a mode where the
    # model can act but cannot ask is a mode where it has to guess.
    modes = set(TaskMode)

    def approval_summary(self, args: AskUserArgs) -> str:
        return f"Ask: {args.question}"

    async def execute(self, args: AskUserArgs, ctx: ToolContext) -> ToolResult:
        options = [{"label": o.label, "description": o.description} for o in args.options]
        return ToolResult(
            ok=True,
            # The content is what the MODEL sees in history. It records that the
            # question was put and that nothing has come back yet, so a replayed
            # transcript never reads as though the user already answered.
            content=(
                f"Asked the user: {args.question}\n"
                f"Options: {', '.join(o['label'] for o in options)}\n"
                "Awaiting their reply — it arrives as the next user message. "
                "Do not answer on their behalf."
            ),
            summary=args.question if len(args.question) <= 60 else args.question[:59] + "…",
            detail=f"{len(options)} options",
            ask={"question": args.question, "options": options, "multi": args.multi},
        )
