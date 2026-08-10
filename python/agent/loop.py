"""The agent loop: model -> (tokens | tool calls) -> execute -> feed back -> repeat.

Built from scratch (no framework) because the loop IS the product's security
core; every line must be auditable. Frameworks hide exactly the steps we need
to interpose on: approval gates, output scanning, capability checks.

Failure design — the model is assumed to be unreliable:
  * malformed args        -> validation error goes back as a tool result; the
                             model gets a chance to correct itself
  * unknown/out-of-mode   -> "tool not available" result (never an exception;
                             injected text asking for email_send in research
                             mode just gets told no)
  * tool crash            -> error result, loop continues
  * runaway               -> hard iteration cap from Settings
  * denial                -> the model is TOLD the user declined, so it can
                             answer gracefully instead of hallucinating success
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from agent.registry import ToolRegistry
from core import events
from core.ollama_client import OllamaClient
from security.approvals import ApprovalBroker
from security.gateway import SecurityGateway
from tools.base import Risk, TaskMode, ToolContext

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


def _call_key(call: dict[str, Any]) -> str:
    """Identity of a call, for spotting an exact repeat within one turn.

    Arguments are included and key-sorted: reading two different files is two
    pieces of work, reading the same file twice is a loop.
    """
    import json

    try:
        args = json.dumps(call.get("arguments") or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args = str(call.get("arguments"))
    return f"{call.get('name')}:{args}"


def _call_shape(obj: Any) -> dict[str, Any] | None:
    """{"name": str, arguments: dict} -> a normalised call, else None.

    The `or` chain this replaces (`obj.get("arguments") or obj.get("parameters")`)
    had a quiet bug: an EMPTY dict is falsy, so a call with no arguments —
    `{"name": "list_files", "arguments": {}}` — fell through every branch and was
    never recognised. Zero-argument tools are exactly the ones a model reaches
    for first when orienting itself in a project.
    """
    if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
        return None
    for key in ("arguments", "parameters", "args"):
        if key in obj and isinstance(obj[key], dict):
            return {"name": obj["name"], "arguments": obj[key]}
    return None


def recover_text_tool_call(text: str) -> dict[str, Any] | None:
    """Salvage a tool call the model wrote as PROSE instead of emitting
    structurally — e.g. printing {"name": "email_send", "parameters": {...}}
    into the chat. Small local models do this regularly (their chat template
    fails to catch the tool syntax), and without recovery the user sees JSON
    soup and nothing happens.

    Safe to be lenient here: a recovered call goes through the exact same
    gates as a native one — mode check, Pydantic validation, approval dialog.
    Recovery changes how the request is DETECTED, never what it's ALLOWED."""
    import json

    decoder = json.JSONDecoder()
    idx = 0
    while True:
        start = _next_object(text, idx)
        if start == -1:
            break
        try:
            obj, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        call = _call_shape(obj)
        if call:
            return call
        idx = start + 1

    # Strict decoding never found a valid call. This is the common case for
    # run_python/write_file/email_send: the model's free-text field (code,
    # content, body) contains literal quotes or newlines it never escaped, so
    # the blob isn't valid JSON at all, and the user would otherwise see raw
    # JSON soup with a code block embedded in it (this is exactly what
    # happened with a run_python call whose code contained an unescaped
    # f-string quote). Try one more salvage pass before giving up.
    start = _next_object(text, 0)
    if start != -1:
        return _salvage_unescaped_field(text, start)
    return None


# `{` then ANY whitespace then `"`. The whitespace is the whole point.
#
# THE BUG THIS FIXES. This scan used to look for the literal strings '{"' and
# '{ "', which matches compact JSON and nothing else. A model that pretty-prints
# its call —
#
#     {
#       "name": "find_files",
#       "arguments": {"pattern": "login.css"}
#     }
#
# — opens with '{\n  "', so the scan never found it, recovery never ran, and the
# loop treated a tool call as the final answer. On screen that looked like
# Arthur announcing "I'll search for it", printing a JSON code block, and
# stopping dead. Every turn. Pretty-printing is the DEFAULT for coder-tuned
# models, which is why it showed up the moment a coder model was used.
_OBJ_START = re.compile(r'\{\s*"')


def _next_object(text: str, idx: int) -> int:
    m = _OBJ_START.search(text, idx)
    return m.start() if m else -1


# A ```json fence around the blob, which is how coder models present it.
_FENCE_OPEN = re.compile(r"```[a-zA-Z]*\s*$")


def strip_tool_call_json(text: str) -> str:
    """Remove tool-call JSON the model typed into its reply.

    WHY THIS IS NOT COSMETIC. When a model writes its call as prose instead of
    emitting it structurally, those characters stream to the screen as ordinary
    tokens — so the user reads a raw `{"name": "edit_file", "arguments": {...}}`
    blob with the whole file contents escaped inside it, and then watches it
    happen again on the next turn. It is the single ugliest thing in the app and
    it explains nothing: the activity row already says "Edited login.css" in
    words. The mechanism by which Arthur asked for a tool is an implementation
    detail; the fact that it edited a file is not.

    It also matters for the MODEL. The cleaned text is what gets persisted and
    replayed as history, so the transcript does not teach it that printing JSON
    into the chat is normal — which is exactly the habit that produced the
    apology-and-retry spiral ("I apologize for the confusion. It seems there was
    an issue with the JSON formatting").

    Only blobs shaped like a tool call are removed: an object with a string
    `name` and a dict of arguments. JSON the user asked to see survives.
    """
    import json

    decoder = json.JSONDecoder()
    spans: list[tuple[int, int]] = []
    idx = 0
    while True:
        start = _next_object(text, idx)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if _call_shape(obj):
            spans.append((start, start + end))
            idx = start + end
        else:
            idx = start + 1

    if not spans:
        # Unparseable blobs (the unescaped-field case) still reach the screen.
        # Cut from the opening brace to the end: a call the strict decoder
        # cannot read is always the tail of the message, because the thing that
        # broke it was raw file content running to the end.
        start = _next_object(text, 0)
        if start != -1 and '"name"' in text[start:start + 60]:
            spans = [(start, len(text))]
        else:
            return text

    out = text
    for start, end in reversed(spans):
        # Swallow the enclosing ``` fence if there is one, so removing the blob
        # does not leave an empty code block behind.
        head, tail = out[:start], out[end:]
        m = _FENCE_OPEN.search(head.rstrip("\n") + "\n")
        if m:
            head = head[:head.rstrip().rfind("```")]
        stripped = tail.lstrip()
        if stripped.startswith("```"):
            tail = stripped[3:]
        out = head.rstrip() + "\n\n" + tail.lstrip()

    return out.strip()


def _salvage_unescaped_field(text: str, start: int) -> dict[str, Any] | None:
    """Recovers {"name": ..., "arguments"/"parameters": {...}} when the LAST
    field in the arguments object is free text containing raw quotes/newlines
    that break strict JSON parsing. Every tool whose schema has one large
    text field (run_python's code, write_file's content, email_send's body)
    declares that field last, so "take everything from the last field's
    opening quote to the end of the blob, literally" recovers exactly the
    payload the model meant to send.

    Field boundaries are found only by anchoring immediately after
    `params_start` or immediately after a PRECEDING field's own comma —
    never by scanning ahead into a value for something that merely looks
    like "key": "value". That distinction matters: free text is exactly the
    kind of content that contains lookalikes of its own (a Python dict
    literal, an embedded JSON fragment) and an unanchored scan mistakes those
    for the next real argument, truncating the recovered text at the first
    one. Anchoring rules that out structurally instead of by luck.

    Recovery result still goes through Pydantic validation before anything
    runs, so a wrong guess here surfaces as a normal "invalid arguments" tool
    result, not a security hole."""
    import json
    import re

    m_name = re.search(r'"name"\s*:\s*"([a-zA-Z_][a-zA-Z0-9_]*)"', text[start:])
    if not m_name:
        return None
    name = m_name.group(1)

    # Both the colon AND the key's closing quote are optional here.
    #
    # The colon being optional was already intended. What this missed is WHERE
    # the quote lands when a model drops the colon: it does not write
    # `"parameters" {`, it writes `"parameters {"` -- the brace ends up INSIDE
    # the quoted key and the closing quote lands after it. The old pattern
    # required `"parameters"` as a complete token before the brace, so it never
    # matched the real malformation and recovery returned None for every
    # email_send call that hit it.
    #
    # Not consuming that trailing quote is deliberate: in the malformed form it
    # is exactly the opening quote of the first argument, so leaving it makes
    # the remainder parse identically to the well-formed case below.
    m_params = re.search(r'"(?:arguments|parameters|args)\s*"?\s*:?\s*\{', text[start:])
    if not m_params:
        return None
    rest = text[start + m_params.end():]

    # A clean leading field: value has no unescaped quote AND is immediately
    # followed by a comma. Requiring the comma is what keeps this from ever
    # matching partway into a free-text value -- a genuine next field is
    # comma-separated; a lookalike inside a value usually isn't followed by
    # one at exactly that spot.
    clean_field = re.compile(r'\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,')
    final_field = re.compile(r'\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:\s*"')
    # List-typed args (email_send's `to`/`cc`/`bcc`) sometimes come back as a
    # JSON array that the model then wrapped in an extra pair of quotes --
    # "to": "["a@x.com"]" instead of "to": ["a@x.com"]. clean_field can't match
    # that (the bare quote right after "[" looks like the value's closing
    # quote), so it's tried FIRST and, when it parses, produces a real list
    # instead of a string Pydantic would reject outright.
    array_field = re.compile(r'\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:\s*"(\[[^\]]*\])"\s*,')

    arguments: dict[str, Any] = {}
    pos = 0
    while True:
        m = array_field.match(rest, pos)
        if m:
            try:
                arguments[m.group(1)] = json.loads(m.group(2))
            except json.JSONDecodeError:
                arguments[m.group(1)] = _unescape(m.group(2))
            pos = m.end()
            continue
        m = clean_field.match(rest, pos)
        if not m:
            break
        arguments[m.group(1)] = _unescape(m.group(2))
        pos = m.end()

    m_final = final_field.match(rest, pos)
    if not m_final:
        return {"name": name, "arguments": arguments} if arguments else None

    field = m_final.group(1)
    value_start = m_final.end()
    tail = rest.rstrip()
    end = len(tail)
    while end > value_start and tail[end - 1] == "}":
        end -= 1
    if end > value_start and tail[end - 1] == '"':
        end -= 1
    if end <= value_start:
        return None
    arguments[field] = _unescape(tail[value_start:end])

    return {"name": name, "arguments": arguments}


def _unescape(raw: str) -> str:
    """Undo the escapes a well-formed emitter WOULD have used (\\n, \\t, \\",
    \\\\), leaving any other literal character — including the raw quotes
    that broke strict parsing in the first place — exactly as written."""
    return (
        raw.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


# What each capability is called in plain words, and which tools provide it.
# Used to tell the model what it CANNOT do this turn, computed from what it was
# actually granted — see _with_capability_note.
OTHER_CAPABILITIES = {
    "sending or reading email": {"email_send", "email_list", "email_search"},
    "opening or controlling apps on this computer":
        {"open_app", "screenshot", "mouse_click", "type_text", "press_keys"},
    "searching the web": {"web_research", "quick_search"},
    "running code": {"run_python"},
    "reading or editing files in the user's folder":
        {"read_file", "list_files", "write_file", "edit_file", "delete_file",
         "search_files", "find_files"},
    "looking up market data": {"stock_quote", "stock_history"},
}


def _with_capability_note(
    messages: list[dict[str, Any]], mode: TaskMode, tools: list[Any],
) -> list[dict[str, Any]]:
    """Tell the model, in the system prompt, exactly what it can do THIS TURN.

    THE BUG THIS FIXES. Mode scoping is enforced in the registry, so a tool
    outside the current mode simply does not exist in the model's world. That
    stops the tool from RUNNING — it does nothing to stop the model from saying
    it ran. Asked to send an email in Code mode, a small local model answered
    "Done. Email sent to …" and then "Opened Discord for you." Nothing was sent,
    nothing was opened, no tool was called: it pattern-matched a plausible reply
    and asserted it. Silent capability scoping plus a confident model is a
    machine for producing lies.

    The fix is to make the boundary VISIBLE. The model cannot infer what it is
    missing from an empty tool list — absence is not information — so it gets
    told: here is your complete set of actions, anything else belongs to another
    mode, say so rather than claiming it.

    Generated from the registry rather than written by hand, so it cannot drift
    out of date when a tool is added, moved between modes, or removed.
    """
    granted = {t.name for t in tools}
    names = ", ".join(t.name for t in tools) if tools else "none"
    # THE "you cannot do this" EXAMPLES ARE DERIVED, NEVER HARDCODED.
    #
    # This list used to be a fixed sentence ending "...running code, editing
    # files — you CANNOT do it here", which is true in most modes and FALSE in
    # the one mode built for editing files. So Code mode handed the model a
    # granted `edit_file` tool and, two lines later, told it that editing files
    # was impossible. Asked to scan a folder, a 7B resolved the contradiction
    # the way models do — it believed the prohibition and answered "I don't have
    # access to files or folders on your computer".
    #
    # Deriving the list from what is actually MISSING makes the note incapable
    # of contradicting the tools beside it.
    missing = [label for label, owners in OTHER_CAPABILITIES.items() if not (owners & granted)]
    cannot = ", ".join(missing) if missing else "anything outside the list above"
    note = (
        f"CAPABILITIES — you are in {mode.value.upper()} mode and your ONLY available "
        f"actions this turn are: {names}.\n"
        "Arthur separates capabilities by mode on purpose, and you have no way to reach "
        f"a tool that is not listed above. You CANNOT do these here: {cannot}. If the "
        "user asks for one, say so plainly in one sentence and name the mode that can "
        "(General, Research, Code, Email, Finance, Computer, Design); the user "
        "switches modes from the icons on the left.\n"
        "NEVER say an action is done, sent, opened, saved or created unless you actually "
        "called a tool above and saw it succeed. Claiming a completed action you did not "
        "perform is the worst thing you can do in this app.\n"
        "NEVER write out what a tool call or a tool result LOOKS LIKE — no JSON call "
        "objects, no <<EXTERNAL>> blocks, no invented file contents. Call the tool and "
        "wait for the real result. Writing the shape of an answer instead of getting one "
        "is the same lie as the paragraph above.\n"
        "Do not ask permission before using a tool. The app already asks the user "
        "whenever a confirmation is genuinely needed, so a question from you just stalls "
        "the work. Take the next step.\n"
        "YOU are the one who runs these tools — the user cannot. Never write a plan that "
        "tells them which tool to use, never say 'let me know what you find', and never "
        "ask them to report a result back to you. If you catch yourself describing a "
        "step, make the call instead."
    )
    if messages and messages[0].get("role") == "system":
        # Copy rather than mutate: `messages` belongs to the caller, and the
        # note is per-turn — appending in place would stack a fresh copy onto
        # the system prompt on every iteration of a long conversation.
        return [{**messages[0], "content": messages[0]["content"] + "\n\n" + note}, *messages[1:]]
    return [{"role": "system", "content": note}, *messages]


# A fenced block big enough to be a file rather than an inline example. Same
# shape chat_service._warn_if_code_was_only_printed uses to detect this after
# the fact — defined again here because the loop needs it BEFORE the turn
# ends, to prevent the miss instead of just reporting it.
_CODE_BLOCK = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
_MIN_BLOCK_LINES = 5


def _largest_code_block(text: str) -> str | None:
    blocks = [b for b in _CODE_BLOCK.findall(text or "") if len(b.splitlines()) >= _MIN_BLOCK_LINES]
    return max(blocks, key=len) if blocks else None


# A reply ASSERTING that a file was changed.
#
# WHY THIS EXISTS SEPARATELY FROM THE CODE-BLOCK CHECK. Recovery used to trigger
# only when the model printed a file into the chat, which quietly made it depend
# on the model being verbose. Observed: the user said "just apply it, no need to
# show me the code", the model obliged — prose only, no fenced block — and
# answered "Changes staged for review." Nothing had been staged; it was echoing
# write_file's own description back. No block meant no recovery, so the one
# phrasing that most clearly means "I did the thing" was the one phrasing that
# turned the safety net off.
#
# Past tense only: "I'll edit login.css" is a plan and must not match, while
# "I've updated" and "changes staged" are claims of completed work. `staged` on
# its own is included deliberately — it is OUR vocabulary, from our tool
# descriptions, and a model using it is describing our machinery rather than
# reporting anything it observed.
_CLAIMED_CHANGE = re.compile(
    r"\bstaged\b"
    r"|\bi(?:'ve| have)?\s+(?:just\s+)?(?:updated|changed|edited|modified|applied|saved|"
    r"rewritten|replaced)\b"
    r"|\bchanges?\s+(?:have\s+been\s+|has\s+been\s+|were\s+|was\s+|are\s+|is\s+)?"
    r"(?:staged|applied|saved|made|written)\b"
    r"|\b(?:file|it)\s+(?:has\s+been|have\s+been|is\s+now|was|were)\s+"
    r"(?:updated|changed|edited|saved|written|modified)\b",
    re.IGNORECASE,
)


def claims_a_file_change(text: str) -> bool:
    return bool(_CLAIMED_CHANGE.search(text or ""))


# How many times one turn may be forced into a structured call. Two is enough
# to convert a plan into action without letting a model that genuinely has
# nothing to run spin.
MAX_FORCED = 2

# ONLY THE PATH IS ASKED FOR. The content is taken from the block already on
# screen.
#
# THE BUG THIS FIXES. The first version of this asked the model to re-emit the
# whole file as a JSON string through constrained decoding. An 80-line CSS file
# emitted character-by-character through a grammar, on a small local model, does
# not finish inside FORCE_TIMEOUT_S — so the call raised, the recovery returned
# None, and the turn ended having done nothing. Silent, and indistinguishable
# from the model simply refusing.
#
# Asking for a path is three tokens. And the block the model already committed
# to on screen is a better source for the content than a second attempt at
# reproducing it, which small models quietly shorten.
FORCE_PATH_PROMPT = (
    "You did not actually call a tool, so the user's file is UNCHANGED — "
    "whatever you printed or said about it did not happen. Which file were you "
    "editing? Answer with its path relative to the project folder."
)

# The fragment case: what was printed is only PART of the file, so it has to go
# in as an edit. The model has the file's real text in this turn's history (it
# read it), which is what makes copying old_text verbatim a reasonable ask.
FORCE_EDIT_PROMPT = (
    "You printed only part of {path}, so it cannot replace the whole file. Give "
    "the exact snippet to find (old_text, copied character-for-character from "
    "the file you read) and what to put in its place (new_text)."
)

# A printed block counts as a whole-file replacement only if it is at least this
# fraction of the file it claims to replace.
#
# WHY A GUARD AT ALL. Observed with qwen2.5-coder:7b: asked to recolour an
# 82-line stylesheet, it printed the TWO rules it wanted to change. Writing that
# as the file would have deleted sixty lines of working CSS, including the
# background image the user explicitly asked to keep. The undo makes that
# survivable; it does not make it acceptable. A model showing you the part it
# changed is normal, correct behaviour — the mistake would be ours, in reading
# an excerpt as a replacement.
WHOLE_FILE_RATIO = 0.6

# Longer than the path call, shorter than a full generation: an edit re-emits
# only the changed region, so it is quick, but it is real text and 25s is tight
# for it on a small local model.
FORCE_EDIT_TIMEOUT_S = 45.0

# Much shorter than chat_json's 150s default. This is a RECOVERY path: the user
# already has the model's text, and waiting two minutes to maybe upgrade it into
# a tool call is worse than not trying. A turn that appears to hang is the one
# failure people cannot tell apart from a crash.
FORCE_TIMEOUT_S = 25.0

PICK_PROMPT = (
    "You described a step but did not call a tool, so nothing happened. "
    "Which tool did you mean to use? Answer with its name, or \"none\" if you were "
    "not trying to use one."
)
ARGS_PROMPT = "Give the arguments for {name}, based on what you just said you would do."


class AgentLoop:
    def __init__(
        self,
        llm: OllamaClient,
        registry: ToolRegistry,
        gateway: SecurityGateway,
        approvals: ApprovalBroker,
        max_iterations: int = 6,
    ):
        self._llm = llm
        self._registry = registry
        self._gateway = gateway
        self._approvals = approvals
        self._max_iterations = max_iterations

    async def run(
        self,
        model: str,
        messages: list[dict[str, Any]],
        mode: TaskMode,
        ctx: ToolContext,
        emit: Emit,
        max_iterations: int | None = None,
    ) -> str:
        """Streams via `emit`, returns the final assistant text.

        `max_iterations` overrides the constructor default for THIS run. The
        right ceiling is a property of the task, not of the loop: one email is
        two calls, editing a project is forty. Passing it per run keeps that
        judgement with the caller that knows the mode (see chat_service) rather
        than freezing one number for every mode at startup.
        """
        limit = max_iterations or self._max_iterations
        tools = self._registry.for_mode(mode)
        schemas = [t.to_ollama_schema() for t in tools] or None
        messages = _with_capability_note(messages, mode, tools)

        # TOOLS ARE DROPPED WHEN THE TURN CARRIES AN IMAGE.
        #
        # Vision and tool-calling are handled by different branches of a model's
        # chat template, and on small local models the combination is routinely
        # mishandled -- the template renders the tool block and drops the image
        # part, so the model reports it received no image. That is exactly the
        # symptom here: the bytes are verifiably base64 and on the last user
        # message, Ollama reports `vision` for the model, and it still answers
        # "I cannot see an image".
        #
        # Looking at a picture is not a task that needs tools, so giving them up
        # for that one turn costs nothing real. If the model wants a tool it can
        # ask in the next turn, which carries no image and gets the full set.
        has_images = any(m.get("images") for m in messages)
        if has_images and schemas:
            log.info("dropping %d tool schemas for this turn: it carries an image", len(schemas))
            schemas = None

        final_text_parts: list[str] = []
        forced = 0
        executed: set[str] = set()

        for _iteration in range(limit):
            tokens: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            async for event in self._llm.chat_stream(model, messages, tools=schemas):
                if event["type"] == "token" and event["content"]:
                    tokens.append(event["content"])
                    await emit(events.TOKEN, {"content": event["content"]})
                elif event["type"] == "tool_calls":
                    tool_calls = event["calls"]

            text = "".join(tokens)
            if text:
                final_text_parts.append(text)

            if not tool_calls and tools:
                # Second chance: did the model write the call as text?
                recovered = recover_text_tool_call(text)
                if recovered and not self._registry.get_granted(recovered["name"], mode):
                    # RECOGNISED BUT NOT AVAILABLE HERE — e.g. the model tried
                    # to read a file while in General mode.
                    #
                    # The JSON gets stripped anyway. Whether Arthur COULD run
                    # the call has nothing to do with whether the user should
                    # have to read the blob, and this is the case where it is
                    # least excusable: nothing happened, so the only thing on
                    # screen is machine syntax describing a non-event.
                    #
                    # Then say what went wrong in words. Without this the user
                    # sees an answer that simply stops, with no hint that the
                    # mode is the reason.
                    cleaned = strip_tool_call_json(text)
                    if cleaned != text:
                        final_text_parts[-1:] = [cleaned] if cleaned else []
                        await emit(events.DRAFT_REPLACE, {"content": "".join(final_text_parts)})
                    await emit(events.STATUS, {
                        "text": (f"Tried to use {recovered['name']}, which isn't available in "
                                 f"{mode.value} mode — switch modes on the left to allow it."),
                    })
                elif recovered and self._registry.get_granted(recovered["name"], mode):
                    # Take the JSON back off the screen. The tokens are already
                    # there — nothing can stop that mid-stream — so the draft is
                    # replaced with the cleaned version, and the cleaned version
                    # is what gets persisted and replayed as history.
                    #
                    # No STATUS line here any more. "Understood — running
                    # read_file (the model wrote it as text)" narrated our own
                    # plumbing at the user: they did not ask for a tool by name
                    # and cannot act on how it was parsed. The activity row says
                    # "Read login.css", which is the part that is about them.
                    text = strip_tool_call_json(text)
                    final_text_parts[-1:] = [text] if text else []
                    await emit(events.DRAFT_REPLACE, {"content": "".join(final_text_parts)})
                    tool_calls = [recovered]

                # THIRD CHANCE: no call came out, whatever the reason.
                #
                # WHY THE TURN "JUST STOPS". The loop ends when the model asks
                # for no tools — which is correct for an assistant that has
                # finished answering, and wrong for one that has finished
                # WRITING A PLAN. Small models do the latter constantly: a tidy
                # numbered list saying "1. Use find_files with '*login*'", then
                # "let me know what you find!", and the turn is over because
                # nothing executable was ever emitted.
                #
                # THE TRIGGER USED TO REQUIRE THE REPLY TO NAME A TOOL, which
                # caught "I'll use find_files" and missed the worse case: a model
                # that skips straight to inventing the ANSWER. "I've scanned your
                # folder and found: README.md, project.py, data.csv" names no
                # tool, ran nothing, and listed three files that do not exist.
                #
                # So the trigger is now simply "no call came out". The escape
                # hatch is in the schema instead: "none" is one of the legal
                # answers, so a turn that genuinely needed no tool costs one
                # short constrained call and nothing else. Paying that on every
                # toolless turn is worth not shipping fabricated directory
                # listings.
                if not tool_calls and forced < MAX_FORCED:
                    forced += 1
                    # THE WORST CASE FIRST: the model printed a whole file into
                    # the chat instead of saving it. _forced_tool_call's own
                    # PICK_PROMPT offers "none" as a legitimate answer — right
                    # for a turn that truly needed no tool, wrong here, because
                    # the model just showed the user a finished-looking file and
                    # believes THAT was the deliverable. Asked "were you trying
                    # to use a tool?" it honestly answers "none", and the edit
                    # never lands — the user sees code, says "go ahead", and
                    # gets the same invented block printed a second time.
                    #
                    # So this case is checked and handled before the general
                    # picker ever gets a chance to offer the exit.
                    # EITHER SIGNAL IS ENOUGH: the model printed a file, or it
                    # said it changed one. Requiring the code block made
                    # recovery depend on the model being verbose, so "just
                    # apply it, don't show me the code" switched the safety net
                    # off — see _CLAIMED_CHANGE.
                    block = _largest_code_block(text)
                    call = (await self._force_write(model, messages, text, tools, block, ctx)
                            if block or claims_a_file_change(text) else None)
                    if call is None:
                        call = await self._forced_tool_call(model, messages, text, tools)
                    # NEVER FORCE A CALL THAT ALREADY RAN THIS TURN.
                    #
                    # After a tool succeeds, the model's next message is usually
                    # its ANSWER — "I see three items in your workspace… anything
                    # specific you'd like me to do?" — which contains no tool
                    # call because none is needed. Forcing there asked a small
                    # model "which tool did you mean?", it picked the one it had
                    # just used, list_files ran a second time, and the model
                    # repeated its answer verbatim. On screen: the same
                    # paragraph twice, for no reason.
                    #
                    # Comparing against what actually executed is the precise
                    # test. It still allows forcing a DIFFERENT next step, which
                    # is the whole point on a multi-file task.
                    if call and _call_key(call) in executed:
                        log.info("skipped a forced repeat of %s", call["name"])
                        call = None
                    if call and self._registry.get_granted(call["name"], mode):
                        tool_calls = [call]

            if not tool_calls:
                return "".join(final_text_parts)

            # Record the assistant turn that requested the calls (required by
            # the chat template so the model sees its own call in history).
            messages.append({
                "role": "assistant",
                "content": text,
                "tool_calls": [
                    {"function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in tool_calls
                ],
            })

            for call in tool_calls:
                executed.add(_call_key(call))
                result_msg = await self._execute_one(call, mode, ctx, emit)
                messages.append(result_msg)

        # Hitting the cap in Code mode is not the same event as hitting it
        # elsewhere. Everywhere else the turn just ends; here it can end with a
        # PARTIALLY written changeset, which is indistinguishable from a
        # finished one in the review panel. Saying so is the difference between
        # the user reading the diff and the user trusting it.
        note = (" Some edits may be half-finished — read the diff before applying, "
                "or ask Arthur to carry on." if mode is TaskMode.CODE else "")
        await emit(events.STATUS, {
            "text": f"Stopped: reached the tool-use limit for one message.{note}",
        })
        await emit(events.TOOL_LIMIT, {"mode": mode.value})
        return "".join(final_text_parts)

    async def _forced_tool_call(
        self, model: str, messages: list[dict[str, Any]], said: str, tools: list[Any],
    ) -> dict[str, Any] | None:
        """Turn "I'll use find_files" into an actual call, using constrained decoding.

        WHY THIS EXISTS, and why it is not another prompt.

        The loop ends when the model asks for no tools. That is right for an
        assistant that finished ANSWERING and wrong for one that finished
        writing a PLAN — and small models write plans constantly: a tidy
        numbered list saying "1. Use find_files with '*login*'", addressed to
        the user as though they had the tools. Nothing executable is emitted, so
        the turn dies.

        The first attempt at this was a prose nudge ("you did not actually call
        the tool, make the call now"), which is just asking the same model to do
        the same thing it already failed at. This instead REMOVES the failure
        mode: OllamaClient.chat_json compiles a JSON schema into a decoding
        grammar, so tokens that would break the shape are unreachable. The model
        cannot answer with a plan, an apology, or malformed JSON — the only
        legal outputs are a tool name, then that tool's own arguments.

        Two calls rather than one, because a union-of-all-tools schema is
        awkward and small models pick badly from it: first the name from a short
        enum, then the arguments from THAT tool's Pydantic schema, which we
        already generate for the native tool-calling path.

        The result goes through every normal gate afterwards — mode check,
        Pydantic validation, approval. Forcing changes how a call is OBTAINED,
        never what it is allowed to do.
        """
        names = [t.name for t in tools]
        history = [*messages, {"role": "assistant", "content": said}]
        try:
            pick = await self._llm.chat_json(
                model,
                [*history, {"role": "user", "content": PICK_PROMPT}],
                {"type": "object",
                 "properties": {"tool": {"type": "string", "enum": [*names, "none"]}},
                 "required": ["tool"]},
                timeout_s=FORCE_TIMEOUT_S,
            )
            name = (pick or {}).get("tool")
            # "none" is a real answer, not a failure: a model may name a tool
            # while explaining rather than while intending to use one.
            if not name or name == "none":
                return None
            tool = next(t for t in tools if t.name == name)
            args = await self._llm.chat_json(
                model,
                [*history, {"role": "user", "content": ARGS_PROMPT.format(name=name)}],
                tool.Args.model_json_schema(),
                timeout_s=FORCE_TIMEOUT_S,
            )
        except Exception as e:
            # Never let this break the turn. Without it the user still gets the
            # model's text, which is the behaviour we had before.
            log.info("forced tool call failed: %s", e)
            return None
        log.info("forced a structured call to %s after the model described it", name)
        return {"name": name, "arguments": args if isinstance(args, dict) else {}}

    async def _force_write(
        self, model: str, messages: list[dict[str, Any]], said: str,
        tools: list[Any], block: str | None, ctx: ToolContext,
    ) -> dict[str, Any] | None:
        """Turn code printed into the chat into a real file change.

        THE FAILURE THIS EXISTS FOR, verbatim from a session: asked to recolour
        login.css, the model read the file, printed the updated CSS into the
        chat, and stopped. The user said "I authorize you, apply it" and got the
        same block printed again. Three times. Nothing was ever staged.

        `_forced_tool_call` cannot rescue that, because its PICK step offers
        "none" as a legal answer — correct for a turn that genuinely needed no
        tool, wrong here, since a model that just showed the user a finished
        file believes that WAS the deliverable and answers honestly.

        WHOLE FILE vs FRAGMENT is the decision that matters, and getting it
        wrong is destructive: a model showing you the two rules it changed is
        behaving correctly, and treating that excerpt as a replacement would
        delete the rest of the file. So the printed block is measured against
        what is actually there, and only a plausible whole-file rewrite goes in
        through write_file. A fragment goes in as an edit, whose failure mode is
        safe — a mismatched old_text is refused by the tool with an explanation
        the model can act on, rather than silently destroying sixty lines.

        No step here re-emits the file. See FORCE_PATH_PROMPT.
        """
        write = next((t for t in tools if t.name == "write_file"), None)
        edit = next((t for t in tools if t.name == "edit_file"), None)
        changes = getattr(ctx, "changes", None)
        # Without a changeset nothing can be staged anyway, and without it there
        # is no way to read the current file — so no way to tell a rewrite from
        # an excerpt. Refusing to guess is the whole point of the guard.
        if write is None or changes is None:
            return None

        history = [*messages, {"role": "assistant", "content": said}]
        try:
            picked = await self._llm.chat_json(
                model, [*history, {"role": "user", "content": FORCE_PATH_PROMPT}],
                {"type": "object", "properties": {"path": {"type": "string"}},
                 "required": ["path"]},
                timeout_s=FORCE_TIMEOUT_S,
            )
        except Exception as e:
            log.info("forced write failed while asking for the path: %s", e)
            return None
        path = (picked or {}).get("path") if isinstance(picked, dict) else None
        if not path or not isinstance(path, str):
            return None

        try:
            current, _state = changes.read(path)
        except Exception as e:
            # Broad ON PURPOSE, like every other arm of this recovery path. The
            # model just invented a path string, so this is where a traversal
            # attempt ("../../etc/passwd"), an absolute path or an unreadable
            # file lands. safe_path has already refused it — the only question
            # left is whether that refusal ends the user's turn, and it must
            # not: they still have the model's answer, and a forced call that
            # cannot be made is exactly the old behaviour.
            log.info("forced write: cannot read %s: %s", path, e)
            return None

        # NO BLOCK AT ALL means the model asserted the change in prose ("Changes
        # staged for review") without showing anything. There is no content to
        # write, so the only honest move is to make it produce a real edit --
        # which it can, because it read the file earlier this turn.
        if block is not None and (current is None or len(block) >= len(current) * WHOLE_FILE_RATIO):
            # A new file, or a block big enough to plausibly BE the file.
            log.info("forced write_file for %s after the model only printed it", path)
            return {"name": "write_file", "arguments": {"path": path, "content": block}}

        if edit is None:
            return None
        try:
            args = await self._llm.chat_json(
                model,
                [*history, {"role": "user", "content": FORCE_EDIT_PROMPT.format(path=path)}],
                edit.Args.model_json_schema(),
                timeout_s=FORCE_EDIT_TIMEOUT_S,
            )
        except Exception as e:
            log.info("forced edit failed for %s: %s", path, e)
            return None
        if not isinstance(args, dict) or not args.get("old_text"):
            return None
        # The path is the one already resolved and read, not whatever the second
        # call decided to repeat. Two chances to name the file is two chances to
        # name a different one.
        args["path"] = path
        log.info("forced edit_file for %s: the printed block was a fragment", path)
        return {"name": "edit_file", "arguments": args}

    async def _execute_one(
        self, call: dict[str, Any], mode: TaskMode, ctx: ToolContext, emit: Emit
    ) -> dict[str, Any]:
        name, raw_args = call["name"], call.get("arguments") or {}

        def tool_msg(content: str) -> dict[str, Any]:
            return {"role": "tool", "tool_name": name, "content": content}

        tool = self._registry.get_granted(name, mode)
        if tool is None:
            await emit(events.TOOL_RESULT, {"name": name, "ok": False, "summary": "not available in this mode", "flagged": False})
            return tool_msg(f"Error: tool '{name}' is not available in {mode.value} mode.")

        try:
            args = tool.Args.model_validate(raw_args)
        except ValidationError as e:
            await emit(events.TOOL_RESULT, {"name": name, "ok": False, "summary": "invalid arguments", "flagged": False})
            return tool_msg(f"Error: invalid arguments for '{name}': {e.errors(include_url=False)}. Retry with corrected arguments.")

        if tool.risk is Risk.CONFIRM:
            approval = self._approvals.create(
                tool=name,
                summary=tool.approval_summary(args),
                args_preview=_preview(args.model_dump()),
                # Structured (unstringified) args, distinct from args_preview:
                # the dialog can seed an editable form from real values (a list
                # stays a list) instead of the comma-joined display string.
                args=args.model_dump(mode="json"),
            )
            await emit(events.APPROVAL_REQUIRED, {
                "id": approval.id, "tool": name, "summary": approval.summary,
                "args_preview": approval.args_preview, "args": approval.args,
            })
            resolution = await self._approvals.wait(approval.id)
            await emit(events.APPROVAL_RESOLVED, {"id": approval.id, "approved": resolution.approved})
            if not resolution.approved:
                return tool_msg(
                    f"The user declined to allow '{name}'. Do not retry it; "
                    "acknowledge and continue without this action."
                )
            if resolution.edited_args is not None:
                # The user rewrote the draft before sending it — e.g. reworded
                # an email or fixed a typo'd address. Re-run it through the
                # SAME validation gate the model's own args went through
                # (never a trusted bypass just because a human typed it).
                try:
                    args = tool.Args.model_validate(resolution.edited_args)
                except ValidationError as e:
                    await emit(events.TOOL_RESULT, {
                        "name": name, "ok": False, "summary": "your edit was invalid", "flagged": False,
                    })
                    return tool_msg(
                        f"The user edited the arguments for '{name}' before approving, but the edit "
                        f"was invalid: {e.errors(include_url=False)}. The action was NOT taken."
                    )

        await emit(events.TOOL_START, {"name": name, "summary": tool.approval_summary(args)})
        try:
            result = await tool.execute(args, ctx)
        except Exception as e:  # a tool bug must not kill the chat stream
            log.exception("tool %s failed", name)
            await emit(events.TOOL_RESULT, {"name": name, "ok": False, "summary": str(e)[:200], "flagged": False})
            return tool_msg(f"Error: '{name}' failed: {e}")

        content, flagged = result.content, False
        if result.external:
            # Untrusted bytes (web pages, emails) get scanned + spotlighted
            # before they may enter model context. Uniform for every tool.
            content, flagged = await self._gateway.scan_tool_output(
                result.source or name, content
            )

        await emit(events.TOOL_RESULT, {
            "name": name, "ok": result.ok,
            "summary": result.summary or ("done" if result.ok else "failed"),
            "detail": result.detail,
            "flagged": flagged,
        })
        msg = tool_msg(content)
        if result.images_b64:
            msg["images"] = result.images_b64  # multimodal models see screenshots
        return msg


def _preview(args: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    """Human-readable arg values for the approval dialog. Lists join with
    commas — str(['a@x.com']) would leak Python syntax ("['a@x.com']") into a
    dialog that non-technical users must be able to read at a glance."""
    out = {}
    for k, v in args.items():
        if isinstance(v, (list, tuple)):
            s = ", ".join(str(item) for item in v)
        else:
            s = str(v)
        out[k] = s if len(s) <= limit else s[:limit] + "…"
    return out
