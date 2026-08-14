"""Tool contract: what every capability must declare before the agent can use it.

Design decisions that carry the security model:

RISK on the class, not in a config file — a tool's risk level is part of its
identity and gets code-reviewed with the tool. SAFE runs immediately
(read-only, reversible). CONFIRM suspends the loop for human approval
(sends email, writes files, clicks your mouse). There is no "trust the model"
tier on purpose.

TASK MODES (privilege separation) — a tool lists the modes it belongs to, and
the registry only hands the model tools for the CURRENT mode. A prompt
injection inside a research page cannot invoke `email_send`: in research mode
that tool does not exist in the model's world. This is capability scoping,
the same idea as OAuth scopes.

PYDANTIC ARGS — the model produces JSON args; Pydantic validates them before
any code runs. Validation errors are fed back to the model as tool results so
it can retry with fixed args (small models frequently need one retry).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


class Risk(enum.Enum):
    SAFE = "safe"
    CONFIRM = "confirm"


class TaskMode(enum.Enum):
    GENERAL = "general"
    RESEARCH = "research"
    EMAIL = "email"
    FINANCE = "finance"
    CODE = "code"
    COMPUTER = "computer"
    DESIGN = "design"


@dataclass
class ToolResult:
    ok: bool
    content: str                      # text fed back to the model
    summary: str = ""                 # short line for the UI activity feed
    # Optional metric shown to the RIGHT of the summary, in mono: "+18 −6",
    # "3 in 2 files", "exit 0". Split from `summary` so the activity feed can
    # align numbers in a column — a run of fifteen tool calls is only scannable
    # if the counts line up instead of trailing off the end of a sentence.
    detail: str = ""
    images_b64: list[str] | None = None  # screenshots -> multimodal models
    external: bool = False            # True => content is untrusted, gateway must spotlight it
    source: str = ""                  # label for the spotlight wrapper
    # A question for the user: {question, options: [{label, description}], multi}.
    # Set, the agent loop ENDS THE TURN after this tool — the model has nothing
    # useful to do until the person answers, and a model that keeps going after
    # asking is a model that answered its own question. Typed as a plain dict so
    # this module, the contract every tool depends on, stays free of UI types.
    ask: dict[str, Any] | None = None
    # Something to DRAW rather than describe:
    #   {kind: "line", title, subtitle, currency, series: [{label, points:
    #    [{t, v}]}], summary}
    #
    # WHY THE UI GETS ITS OWN COPY instead of rendering the model's prose: a
    # price series read back as a sentence is both unreadable and unverifiable,
    # and asking a 7B model to transcribe 250 numbers into a chart spec is
    # asking it to make one up. This payload comes straight from the tool
    # result, so the picture cannot disagree with the data it was built from.
    #
    # `summary` is the accessible one-liner ("NVDA over 1y: up 51.9%, from
    # $78.12 to $118.63"). It is computed HERE, from the same points the chart
    # draws, so the caption can never contradict the line — see the endpoint
    # handling in finance_query._downsample for the other half of that promise.
    #
    # A plain dict, like `ask`, so this module stays free of UI types.
    chart: dict[str, Any] | None = None


class Tool:
    name: str = ""
    description: str = ""
    Args: type[BaseModel] = BaseModel
    risk: Risk = Risk.CONFIRM  # safe-by-default: forgetting to set risk means "ask the human"
    modes: set[TaskMode] = set()

    async def execute(self, args: BaseModel, ctx: "ToolContext") -> ToolResult:
        raise NotImplementedError

    def approval_summary(self, args: BaseModel) -> str:
        """Human-readable line for the approval dialog. Override for clarity."""
        return f"Run {self.name} with {args.model_dump()}"

    def to_ollama_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.Args.model_json_schema(),
            },
        }


@dataclass
class ToolContext:
    """Everything a tool may touch. Deliberately narrow: tools get no direct
    DB handle and no settings object — what a tool can't reach, a hijacked
    tool call can't abuse."""

    conversation_id: str
    workspace_root: str | None = None
    services: dict[str, Any] | None = None  # sandbox runner, vault, graph client…
    # Code mode's pending-edit buffer (coding.changeset.ChangeSet). Typed as
    # Any so this module — the contract every tool depends on — stays free of
    # imports from any one mode's domain package. None outside Code mode, and
    # the file tools treat None as "staging unavailable" rather than falling
    # back to writing straight to disk: failing closed is the whole point.
    changes: Any = None
